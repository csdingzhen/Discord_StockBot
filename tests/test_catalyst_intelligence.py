"""Evidence, cache, budget and two-model tests without billable API calls."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import config
from services import catalyst_intelligence as intelligence, llm_client
from storage import catalyst_store, db, jin10_store, sec_store
from tests.http_fakes import response, session
from tests.test_options_intelligence import DatabaseTestCase, NOW, event


def evidence(as_of=NOW, **changes):
    return {"id": "e1", "source": "jin10", "url": "https://example.test/news/1",
            "text": "AAPL announced a new product.", "primary": False,
            "published_at": (as_of - timedelta(minutes=10)).isoformat(),
            "observed_at": (as_of - timedelta(minutes=5)).isoformat(), **changes}


def answers(classification="known", value=.9):
    return {**{key: {"type": "noul", "noul": value} for key in ("supported", "material", "timely")},
            "classification": {"type": "choice", "choice": classification}}


DRAFT = {"summary": "Possible product catalyst.", "event_type": "product", "event_date": None, "evidence_ids": ["e1"]}


class EvidenceStorageTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        jin10_store.init_db()
        sec_store.init_db()

    async def test_original_publication_and_observation_cutoffs_and_aliases(self):
        rows = [("1", "2026-10-09 22:00:00", "Apple announced product", NOW - timedelta(minutes=5)),
                ("2", "2026-10-10 22:00:00", "AAPL future publication", NOW - timedelta(minutes=5)),
                ("3", "2026-10-09 22:00:00", "AAPL late collection", NOW + timedelta(minutes=5)),
                ("4", "2026-10-09 22:00:00", "NOTAAPL false ticker match", NOW - timedelta(minutes=5))]
        conn = db.get_connection()
        try:
            for key, published, content, fetched in rows:
                conn.execute("INSERT INTO jin10_flash(url,time,content,fetched_at) VALUES(?,?,?,?)",
                    ("https://example.test/" + key, published, content, fetched.isoformat()))
            conn.commit()
        finally:
            conn.close()
        result = catalyst_store.local_evidence("AAPL", ["Apple"], NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["published_at"], "2026-10-09T14:00:00+00:00")

    async def test_sec_summary_reuse_requires_its_own_asof_timestamp(self):
        accession = "0000320193-26-000001"
        sec_store.save_filing({"ticker": "AAPL", "cik": "0000320193", "form_type": "8-K",
            "items": "2.02", "filing_date": "2026-10-09", "accession_number": accession})
        sec_store.save_analysis(accession, '{"summary":"earnings beat"}')
        conn = db.get_connection()
        try:
            conn.execute("UPDATE sec_filings SET fetched_at=?, analyzed_at=?",
                ((NOW - timedelta(hours=1)).isoformat(), (NOW + timedelta(hours=1)).isoformat()))
            conn.commit()
            first = catalyst_store.local_evidence("AAPL", [], NOW)
            self.assertNotIn("earnings beat", first[0]["text"])
            conn.execute("UPDATE sec_filings SET analyzed_at=?", ((NOW - timedelta(minutes=1)).isoformat(),))
            conn.commit()
        finally:
            conn.close()
        result = catalyst_store.local_evidence("AAPL", [], NOW)
        self.assertIn("earnings beat", result[0]["text"])
        self.assertIn("not a primary-source quote", result[0]["text"])
        self.assertIsNone(result[0]["published_at"])

    async def test_calendar_reuses_known_future_schedule_not_future_observation(self):
        conn = db.get_connection()
        try:
            conn.execute("INSERT INTO catalyst_calendar VALUES(?,?,?,?)",
                ("AAPL", "2026-10-30", (NOW - timedelta(hours=1)).isoformat(), '{"date":"2026-10-30"}'))
            conn.execute("INSERT INTO catalyst_calendar VALUES(?,?,?,?)",
                ("AAPL", "2026-11-30", (NOW + timedelta(hours=1)).isoformat(), "{}"))
            conn.commit()
        finally:
            conn.close()
        result = catalyst_store.local_evidence("AAPL", [], NOW)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["event_date"], "2026-10-30")

    async def test_cache_expiry_and_no_future_cache_reads(self):
        catalyst_store.put_cached("key", {"test": True}, NOW)
        self.assertIsNone(catalyst_store.get_cached("key", NOW - timedelta(seconds=1)))
        self.assertEqual(catalyst_store.get_cached("key", NOW + timedelta(seconds=1)), {"test": True})
        self.assertIsNone(catalyst_store.get_cached("key", NOW + timedelta(days=1)))

    async def test_budget_persists_and_unknown_charges_keep_reservation(self):
        with patch.object(config, "CATALYST_DAILY_REQUEST_LIMIT", 10), \
                patch.object(config, "CATALYST_DAILY_BUDGET_USD", .1), \
                patch.object(config, "CATALYST_CALL_RESERVE_USD", .05):
            first = catalyst_store.reserve_call("AAPL", "search", "model")
            catalyst_store.finish_call(first, "error", {}, .5)
            self.assertIsNotNone(catalyst_store.reserve_call("AAPL", "research", "model"))
            catalyst_store.init_db()
            self.assertIsNone(catalyst_store.reserve_call("AAPL", "judge", "model"))

    async def test_atomic_budget_does_not_oversubscribe_concurrent_reservations(self):
        with patch.object(config, "CATALYST_DAILY_REQUEST_LIMIT", 2), \
                patch.object(config, "CATALYST_DAILY_BUDGET_USD", 10):
            ids = await asyncio.gather(*[asyncio.to_thread(catalyst_store.reserve_call, "AAPL", "search", "model") for _ in range(6)])
        self.assertEqual(sum(i is not None for i in ids), 2)

    async def test_reported_cost_tokens_and_latency_are_persisted(self):
        key = catalyst_store.reserve_call("AAPL", "research", "test-model")
        catalyst_store.finish_call(key, "success", {"usage": {"cost": .003, "prompt_tokens": 123, "completion_tokens": 25}}, .2)
        conn = db.get_connection()
        try:
            row = conn.execute("SELECT * FROM catalyst_calls WHERE call_id=?", (key,)).fetchone()
            self.assertEqual((row["cost_usd"], row["prompt_tokens"], row["completion_tokens"], row["latency_seconds"]), (.003, 123, 25, .2))
        finally:
            conn.close()


class ResearchTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        for p in (patch.object(config, "CATALYST_SEARCH_PROVIDER", "none"),
                  patch.object(config, "CATALYST_JUDGE_MODE", "jev"),
                  patch.object(config, "CATALYST_MAX_INPUT_BYTES", 8000),
                  patch.object(config, "CATALYST_DAILY_REQUEST_LIMIT", 30),
                  patch.object(config, "CATALYST_DAILY_BUDGET_USD", 10),
                  patch.object(config, "CATALYST_CONTEXT", {})):
            p.start()
            self.addCleanup(p.stop)

    async def test_empty_local_sources_returns_unexplained_without_llm(self):
        with patch.object(llm_client, "_call_llm", new=AsyncMock()) as chat:
            result = await intelligence.investigate(event(datetime.now(timezone.utc)))
        self.assertEqual(result["classification"], "unexplained")
        self.assertIsNone(result["evidence_score"])
        chat.assert_not_called()

    async def test_market_screen_to_discord_research_and_jev_uses_shared_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from cogs.options_flow import OptionsFlow
        from services import moomoo_rest
        from storage import options_store

        message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(send=AsyncMock(return_value=message))
        bot = Mock()
        bot.get_channel.return_value = channel
        cog = OptionsFlow(bot)
        now = datetime.now(timezone.utc)
        rows = [{"code": f"US.{ticker}261016C00200000", "option_type": "CALL", "left_day": 7,
                 "price": 3, "volume": 10000, "open_interest": 500, "turnover": 3000000,
                 "strike_price": 200, "implied_volatility": 50,
                 "underlying_info": {"price": 190}} for ticker in ("QQQ", "HUM")]
        body = {"data": {"option_list": rows}, "pagination": {"has_more": False, "total": 2}}
        sources = [evidence(now, text="Humana published a CMS announcement.")]
        async def research_chat(*args, **kwargs):
            channel.send.assert_awaited_once()
            self.assertEqual(kwargs["model"], config.CATALYST_RESEARCH_MODEL)
            kwargs["metadata"]["usage"] = {"cost": .002, "prompt_tokens": 200, "completion_tokens": 60}
            return json.dumps(DRAFT)

        with patch.object(config, "CATALYST_ENABLED", True), patch.object(config, "OPTIONS_L3_SCORE", 50), \
                patch.object(config, "OPTIONS_UNIVERSE", "market"), patch.object(config, "MOOMOO_TRANSPORT", "rest"), \
                patch.object(moomoo_rest.MoomooRestClient, "request", new=AsyncMock(return_value=body)), \
                patch.object(cog._rest, "profiles", new=AsyncMock(return_value={"HUM": {"name": "Humana"}})), \
                patch.object(catalyst_store, "local_evidence", return_value=sources), \
                patch.object(llm_client, "_call_llm", new=research_chat), \
                patch.object(llm_client, "call_decisions", new=AsyncMock(return_value={"answers": answers()})) as judge:
            await cog._run_scan()
        judge.assert_awaited_once()
        self.assertEqual(judge.call_args.args[0]["ticker"], "HUM")
        self.assertEqual(options_store.pending_events(datetime.now(timezone.utc)), [])
        self.assertIn("known", message.edit.call_args.kwargs["embed"].fields[-2].value)
        conn = db.get_connection()
        try:
            row = conn.execute("SELECT ticker, delivery, catalyst_json FROM option_events").fetchone()
            self.assertEqual((row["ticker"], row["delivery"]), ("HUM", "sent"))
            self.assertEqual(json.loads(row["catalyst_json"])["status"], "complete")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM catalyst_calls").fetchone()[0], 2)
        finally:
            conn.close()

    async def test_local_first_independent_jev_receives_original_evidence(self):
        now = datetime.now(timezone.utc)
        sources = [evidence(now), evidence(now, url="https://example.test/second", text="Independent regulatory source.")]
        with patch.object(catalyst_store, "local_evidence", return_value=sources), \
                patch.object(config, "CATALYST_SEARCH_PROVIDER", "openrouter"), \
                patch.object(intelligence, "search_evidence", new=AsyncMock()) as search, \
                patch.object(llm_client, "_call_llm", new=AsyncMock(return_value=json.dumps(DRAFT))), \
                patch.object(llm_client, "call_decisions", new=AsyncMock(return_value={"answers": answers()})) as judge:
            result = await intelligence.investigate(event(now, score=75))
        search.assert_not_called()
        self.assertEqual(result["classification"], "known")
        state, questions = judge.call_args.args
        self.assertEqual(state["evidence"][1]["text"], "Independent regulatory source.")
        self.assertIn("proposal_to_verify", state)
        self.assertEqual(questions["supported"]["type"], "noul")

    async def test_cache_hit_skips_research_but_rejudges_current_activity(self):
        now = datetime.now(timezone.utc)
        with patch.object(catalyst_store, "local_evidence", return_value=[evidence(now)]), \
                patch.object(llm_client, "_call_llm", new=AsyncMock(return_value=json.dumps(DRAFT))) as chat, \
                patch.object(llm_client, "call_decisions", new=AsyncMock(return_value={"answers": answers()})) as judge:
            first = await intelligence.investigate(event(now))
            second = await intelligence.investigate(event(datetime.now(timezone.utc), score=95))
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        chat.assert_awaited_once()
        self.assertEqual(judge.await_count, 2)
        self.assertEqual(judge.call_args.args[0]["activity"]["score"], 95)

    async def test_search_error_still_uses_local_evidence(self):
        now = datetime.now(timezone.utc)
        with patch.object(catalyst_store, "local_evidence", return_value=[evidence(now)]), \
                patch.object(config, "CATALYST_SEARCH_PROVIDER", "openrouter"), \
                patch.object(intelligence, "search_evidence", new=AsyncMock(side_effect=llm_client.LLMError("search down"))), \
                patch.object(llm_client, "_call_llm", new=AsyncMock(return_value=json.dumps(DRAFT))), \
                patch.object(llm_client, "call_decisions", new=AsyncMock(return_value={"answers": answers()})):
            result = await intelligence.investigate(event(now))
        self.assertEqual(result["status"], "complete")

    async def test_search_only_accepts_provider_citations_not_model_dates_urls(self):
        async def chat(*args, **kwargs):
            kwargs["metadata"].update({"annotations": [{"type": "url_citation", "url_citation": {
                "url": "https://example.test/verified-link", "title": "Article", "content": "Actual snippet"}}]})
            return '{"url":"https://invented.test/", "date":"2020-01-01"}'
        with patch.object(llm_client, "_call_llm", new=chat):
            rows = await intelligence.search_evidence("AAPL", "Apple earnings", NOW)
        self.assertEqual(rows[0]["url"], "https://example.test/verified-link")
        self.assertIsNone(rows[0]["published_at"])
        self.assertTrue(rows[0]["timing_unverified"])
        result = intelligence.evaluate_judgment(answers(), DRAFT, [{**rows[0], "id": "e1"}])
        self.assertEqual(result["classification"], "possible")
        self.assertLessEqual(result["evidence_score"], 69)

    async def test_historical_research_never_searches_current_web_or_cache(self):
        with patch.object(config, "CATALYST_SEARCH_PROVIDER", "openrouter"), \
                patch.object(intelligence, "search_evidence", new=AsyncMock()) as search, \
                patch.object(catalyst_store, "get_cached") as cached:
            result = await intelligence.investigate(event(NOW - timedelta(days=365)))
        search.assert_not_called()
        cached.assert_not_called()
        self.assertEqual(result["classification"], "unexplained")

    async def test_budget_blocks_llm_before_http(self):
        with patch.object(config, "CATALYST_DAILY_REQUEST_LIMIT", 0), \
                patch.object(catalyst_store, "local_evidence", return_value=[evidence()]), \
                patch.object(llm_client, "_call_llm", new=AsyncMock()) as chat:
            with self.assertRaisesRegex(llm_client.LLMError, "budget"):
                await intelligence.investigate(event())
        chat.assert_not_called()

    async def test_deepseek_judge_mode_and_unsupported_source_ids(self):
        with patch.object(config, "CATALYST_JUDGE_MODE", "deepseek"), \
                patch.object(catalyst_store, "local_evidence", return_value=[evidence()]), \
                patch.object(llm_client, "_call_llm", new=AsyncMock(side_effect=[json.dumps(DRAFT), json.dumps(answers("possible"))])) as chat:
            result = await intelligence.investigate(event(), historical=True)
        self.assertEqual(result["judge_mode"], "deepseek")
        self.assertEqual(chat.await_count, 2)
        with self.assertRaises(llm_client.LLMError):
            intelligence._draft({**DRAFT, "evidence_ids": ["fabricated"]}, {"e1"})

    async def test_invalid_judgment_and_unexplained_score(self):
        for value in (float("nan"), -1, 2, True):
            with self.assertRaises(llm_client.LLMError):
                intelligence.evaluate_judgment(answers(value=value), DRAFT, [evidence()])
        result = intelligence.evaluate_judgment(answers("unexplained"), DRAFT, [evidence()])
        self.assertLessEqual(result["evidence_score"], 39)
        result = intelligence.evaluate_judgment(answers(), {**DRAFT, "evidence_ids": []}, [evidence()])
        self.assertEqual((result["classification"], result["evidence_score"]), ("unexplained", 0))

    async def test_one_dated_citation_cannot_launder_an_untimed_catalyst(self):
        sources = [evidence(), evidence(id="e2", published_at=None, timing_unverified=True)]
        result = intelligence.evaluate_judgment(answers(), {**DRAFT, "evidence_ids": ["e1", "e2"]}, sources)
        self.assertEqual(result["classification"], "possible")
        self.assertLessEqual(result["evidence_score"], 69)

    async def test_context_query_expansion_and_configurable_adapter(self):
        with patch.object(config, "CATALYST_MAX_SEARCHES", 2):
            queries = intelligence.research_queries("HUM", intelligence.company_context("HUM"))
        self.assertIn("CMS Medicare Advantage Star Ratings", queries[1])
        with patch.object(config, "CATALYST_CONTEXT", {"TEST": {"aliases": ["Test Company"], "topics": ["FDA"]}}):
            self.assertEqual(intelligence.company_context("TEST")["topics"], ["FDA"])

    async def test_input_budget_rejects_oversized_instructions_before_http(self):
        with patch.object(config, "CATALYST_MAX_INPUT_BYTES", 1000), \
                patch.object(llm_client, "_call_llm", new=AsyncMock()) as chat:
            with self.assertRaises(llm_client.LLMError):
                await intelligence._chat([{"role": "user", "content": "x" * 2000}], {})
        chat.assert_not_called()

    async def test_jev_uses_decisions_endpoint_not_chat_completions(self):
        http = session(response(body={"answers": answers(), "usage": {"cost": .001, "prompt_tokens": 5}}))
        metadata = {}
        with patch.object(config, "OPENROUTER_API_KEY", "fake"), patch.object(llm_client, "_cooldown_until", 0), \
                patch.object(llm_client.aiohttp, "ClientSession", return_value=http):
            await llm_client.call_decisions({"evidence": []}, intelligence.judge_questions(), metadata=metadata)
        self.assertEqual(http.post.call_args.args[0], "https://openrouter.ai/api/alpha/decisions")
        body = http.post.call_args.kwargs["json"]
        self.assertIn("state", body)
        self.assertIn("questions", body)
        self.assertNotIn("messages", body)
        self.assertEqual(metadata["usage"]["cost"], .001)
