"""Synthetic fixtures, temporary SQLite, and mocked HTTP/Discord only."""
import asyncio
import base64
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from discord.ext import tasks

import config
from cogs import options_flow, scheduler
from services import moomoo_client, moomoo_rest, options_scan
from storage import catalyst_store, db, options_store
from tests.http_fakes import response, session
from utils.market_time import ET

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


class SettingsFixture:
    """Strategy tuning must not change the inputs of deterministic unit tests."""
    def setUp(self):
        super().setUp()
        settings = patch.multiple(config,
            MOOMOO_TRANSPORT="rest", MOOMOO_MAX_PAGES=10, MOOMOO_SCAN_TIMEOUT_SECONDS=120,
            MOOMOO_REQUEST_INTERVAL=1, MOOMOO_SCREEN_FIELD_FILTER={},
            OPTIONS_UNIVERSE="market", OPTIONS_WATCHLIST=[],
            OPTIONS_EXCLUDED_UNDERLYINGS=frozenset({"QQQ", "SPY", "SPX", "SPXW", "IWM", "DIA", "TQQQ"}),
            OPTIONS_SCAN_MINUTES=5, OPTIONS_MIN_PREMIUM=45000, OPTIONS_MIN_CONTRACT_VOLUME=100,
            OPTIONS_MAX_DTE=45, OPTIONS_MAX_SPREAD_RATIO=.3, OPTIONS_MAX_QUOTE_AGE_SECONDS=180,
            OPTIONS_L2_SCORE=35, OPTIONS_L3_SCORE=65, OPTIONS_MIN_HISTORY_DAYS=5,
            OPTIONS_MIN_RELATIVE_VOLUME=2, OPTIONS_MIN_IV_JUMP=5, OPTIONS_SHORT_DTE=14,
            OPTIONS_SCORE_WEIGHTS={}, OPTIONS_EXECUTION_WINDOW_SECONDS=300, OPTIONS_REPEAT_COUNT=3,
            OPTIONS_RETAIN_SNAPSHOTS_DAYS=45, OPTIONS_EVENT_COOLDOWN_MINUTES=60, OPTIONS_MAX_ALERTS_PER_SCAN=4,
            CATALYST_ENABLED=False, CATALYST_MAX_CONCURRENCY=2, CATALYST_TIMEOUT_SECONDS=60,
            CATALYST_MAX_SEARCHES=1, CATALYST_MAX_RESULTS=3, CATALYST_MAX_INPUT_BYTES=8000,
            CATALYST_CACHE_MINUTES=60, CATALYST_LOOKBACK_DAYS=7, CATALYST_CALL_RESERVE_USD=.05,
            CATALYST_DAILY_BUDGET_USD=1, CATALYST_DAILY_REQUEST_LIMIT=30)
        settings.start()
        self.addCleanup(settings.stop)


def snapshot(**changes):
    return {"contract_code": "US.AAPL261016C00200000", "ticker": "AAPL", "option_type": "CALL",
            "strike": 200., "expiry": "2026-10-16", "dte": 7, "last_price": 3., "volume": 10000,
            "open_interest": 500, "turnover": 3000000., "iv": 50., "delta": .35,
            "multiplier": 100., "bid": 2.9, "ask": 3.1, "underlying_price": 190.,
            "observed_at": NOW.isoformat(), "quote_time": None, "source": "moomoo_rest",
            "execution_evidence": False, **changes}


def event(as_of=NOW, **changes):
    snap = snapshot(observed_at=as_of.isoformat())
    contract = options_scan.score_contract(snap, 40, (2000, 500, 5), as_of=as_of)
    return {**options_scan.aggregate_events([contract], as_of)[0], "coverage_complete": True, **changes}


def trade(key="1", **changes):
    return {"id": key, "timestamp": NOW.isoformat(), "quote_time": (NOW - timedelta(milliseconds=300)).isoformat(),
            "price": 3., "size": 200, "multiplier": 100., "bid": 2.9, "ask": 3., **changes}


class ScoringTests(SettingsFixture, unittest.TestCase):
    def test_legacy_normalization_does_not_assume_standard_multiplier(self):
        row = moomoo_client._normalize_option_row({"option_valid": True, "code": "US.AAPL261016C00200000",
            "option_type": "CALL", "turnover": 50000, "option_implied_volatility": float("inf")}, "AAPL")
        self.assertIsNone(row["multiplier"])
        self.assertIsNone(row["iv"])
        self.assertEqual(row["turnover"], 50000)

    def test_volume_without_meaningful_premium_is_rejected(self):
        self.assertIsNone(options_scan.score_contract(snapshot(turnover=200), as_of=NOW))

    def test_reported_turnover_not_last_price_estimate_and_no_assumed_multiplier(self):
        result = options_scan.score_contract(snapshot(last_price=200), as_of=NOW)
        self.assertEqual(result["notional"], 3000000)
        self.assertEqual(result["premium_basis"], "reported_daily_turnover")
        self.assertIsNone(options_scan.score_contract(snapshot(turnover=None, multiplier=None), as_of=NOW))
        result = options_scan.score_contract(snapshot(turnover=None, multiplier=10), as_of=NOW)
        self.assertEqual(result["notional"], 300000)

    def test_zero_and_missing_oi_are_not_infinity_or_opening_activity(self):
        for oi in (0, None, -1):
            result = options_scan.score_contract(snapshot(open_interest=oi), as_of=NOW)
            self.assertIsNone(result["vol_oi_ratio"])
            self.assertLessEqual(result["points"].get("vol_gt_oi", 0), 10)
            self.assertEqual(result["directional_bias"], "unknown")
            self.assertEqual(result["multi_leg_risk"], "unknown")

    def test_history_warmup_and_configurable_weights(self):
        cold = options_scan.score_contract(snapshot(), 40, (2000, 500, 4), as_of=NOW)
        warm = options_scan.score_contract(snapshot(), 40, (2000, 500, 5), as_of=NOW)
        self.assertNotIn("relative_volume", cold["points"])
        self.assertEqual(warm["relative_volume"], 5)
        self.assertGreater(warm["score"], cold["score"])
        with patch.object(config, "OPTIONS_SCORE_WEIGHTS", {"premium_activity": 0}):
            self.assertEqual(options_scan.score_contract(snapshot(), as_of=NOW)["points"]["premium_activity"], 0)

    def test_bad_spreads_invalid_numbers_and_known_stale_quotes(self):
        for changes in ({"bid": 0}, {"ask": 2.8}, {"ask": 10}, {"volume": float("nan")},
                        {"dte": 100}, {"option_type": "unknown"},
                        {"quote_time": (NOW - timedelta(minutes=10)).isoformat()},
                        {"quote_time": (NOW + timedelta(seconds=1)).isoformat()}):
            with self.subTest(changes=changes):
                self.assertIsNone(options_scan.score_contract(snapshot(**changes), as_of=NOW))
        result = options_scan.score_contract(snapshot(bid=None, ask=None), as_of=NOW)
        self.assertFalse(result["quote_age_known"])
        self.assertIsNone(result["spread_ratio"])

    def test_aggressive_passive_put_and_conflicting_classification(self):
        cases = [(trade(), "CALL", "bullish", 80), (trade(price=2.9), "CALL", "bearish", 80),
                 (trade(), "PUT", "bearish", 80), (trade(price=2.95), "CALL", "unknown", 0),
                 (trade(direction="SELL"), "CALL", "unknown", 0),
                 (trade(quote_time=(NOW + timedelta(seconds=1)).isoformat()), "CALL", "unknown", 0),
                 (trade(quote_time=None, direction="BUY"), "CALL", "bullish", 40)]
        for row, kind, bias, confidence in cases:
            result = options_scan.execution_features([row], kind, NOW)
            self.assertEqual((result["directional_bias"], result["direction_confidence"]), (bias, confidence))

    def test_repeat_cluster_premium_dedup_and_bad_trades(self):
        rows = [trade(str(i), size=50) for i in range(3)]
        result = options_scan.execution_features(rows + rows, "CALL", NOW)
        self.assertEqual(result["execution_premium"], 45000)
        self.assertTrue(result["repeated_buying"])
        self.assertTrue(result["meaningful_execution"])
        bad = [trade("cancel", cancelled=True), trade("late", late=True), trade("size", size=0),
               trade("old", timestamp=(NOW - timedelta(hours=1)).isoformat())]
        self.assertEqual(options_scan.execution_features(bad, "CALL", NOW)["trades"], [])

    def test_interval_activity_does_not_use_counter_resets(self):
        previous = snapshot(observed_at=(NOW - timedelta(minutes=5)).isoformat(), volume=9000, turnover=2000000)
        result = options_scan.score_contract(snapshot(), previous=previous, as_of=NOW)
        self.assertEqual((result["interval_volume"], result["interval_turnover"]), (1000, 1000000))
        previous["volume"] = 20000
        self.assertIsNone(options_scan.score_contract(snapshot(), previous=previous, as_of=NOW)["interval_volume"])

    def test_ticker_cluster_dedups_contract_and_flags_possible_multileg(self):
        a = options_scan.score_contract(snapshot(execution_evidence=True, trades=[trade()]), as_of=NOW)
        b = options_scan.score_contract(snapshot(contract_code="US.AAPL261016C00210000", strike=210,
                execution_evidence=True, trades=[trade("2", price=2.9)]), as_of=NOW)
        events = options_scan.aggregate_events([a, a, b], NOW)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]["contracts"]), 2)
        self.assertEqual(events[0]["multi_leg_risk"], "likely")
        self.assertEqual(events[0]["directional_bias"], "unknown")
        self.assertEqual(events[0]["direction_confidence"], 0)
        self.assertLessEqual(events[0]["score"], 100)


class RestSignatureTests(SettingsFixture, unittest.TestCase):
    def test_retry_after_http_date_and_invalid_values(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.assertGreater(moomoo_rest.retry_delay(format_datetime(future), 2), 590)
        self.assertEqual(moomoo_rest.retry_delay("invalid", 2), 2)
        self.assertEqual(moomoo_rest.retry_delay("-3", 2), 0)

    def test_ed25519_and_rsa_sign_exact_transmitted_bytes(self):
        body, query = b'{"code_list":["US.AAPL"]}', "symbol=HUM+CMS"
        canonical = f"123\nPOST\n/api/v1.0/quote/stock-basicinfo\n{query}\n{hashlib.sha256(body).hexdigest()}".encode()
        for key in (ed25519.Ed25519PrivateKey.generate(), rsa.generate_private_key(public_exponent=65537, key_size=2048)):
            signature = base64.b64decode(moomoo_rest.sign_request(key, 123, "POST", "/api/v1.0/quote/stock-basicinfo", query, body))
            if isinstance(key, ed25519.Ed25519PrivateKey):
                key.public_key().verify(signature, canonical)
            else:
                key.public_key().verify(signature, canonical, padding.PKCS1v15(), hashes.SHA256())

    def test_get_uses_empty_hash_not_hash_of_empty(self):
        key = ed25519.Ed25519PrivateKey.generate()
        signature = base64.b64decode(moomoo_rest.sign_request(key, 123, "GET", "/path", "q=x%2Fy", b""))
        key.public_key().verify(signature, b"123\nGET\n/path\nq=x%2Fy\n")

    def test_screen_filters_and_real_unit_normalization(self):
        payload = moomoo_rest.screen_payload([123])
        groups = payload["strategy"]["filter_group_list"]
        self.assertTrue(all(len(group) == 1 for group in groups))
        self.assertEqual(groups[-1]["option_list"][0]["indicator_value"]["value_interval"]["min_value"], 45000000)
        row = {"code": "US.AAPL261016C00200000", "price": 2.5, "implied_volatility": 50.5,
               "multiplier": 100, "strike_price": 200, "turnover": 50000, "option_type": "CALL"}
        normalized = moomoo_rest.normalize_option(row, "AAPL", NOW.isoformat())
        self.assertEqual((normalized["last_price"], normalized["iv"], normalized["turnover"]), (2.5, 50.5, 50000))
        self.assertEqual(normalized["expiry"], "2026-10-16")
        self.assertIsNone(normalized["quote_time"])
        self.assertFalse(normalized["execution_evidence"])


class RestClientTests(SettingsFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.client = moomoo_rest.MoomooRestClient()
        self.client._key = ed25519.Ed25519PrivateKey.generate()
        for p in (patch.object(self.client, "_credentials"), patch.object(config, "MOOMOO_APP_KEY", "test-key")):
            p.start()
            self.addCleanup(p.stop)

    async def request_with(self, *responses):
        http = session()
        http.request = Mock(side_effect=responses)
        with patch.object(moomoo_rest.aiohttp, "ClientSession", return_value=http), \
                patch.object(moomoo_rest.asyncio, "sleep", new=AsyncMock()):
            result = await self.client.request("stock-basicinfo", {"code_list": ["US.AAPL"]})
        return result, http

    async def test_transport_auth_nonce_body_and_no_redirect(self):
        _, http = await self.request_with(response(body={"ret_code": 0, "data": {}}))
        method, url = http.request.call_args.args
        args = http.request.call_args.kwargs
        headers, body = args["headers"], args["data"]
        self.assertFalse(args["allow_redirects"])
        self.assertEqual(headers["X-Api-Key"], "test-key")
        self.assertEqual(len(headers["X-Nonce"]), 32)
        expected = "\n".join((headers["X-Timestamp"], method, url.raw_path, url.raw_query_string,
                               hashlib.sha256(body).hexdigest())).encode()
        self.client._key.public_key().verify(base64.b64decode(headers["Authorization"]), expected)

    async def test_auth_error_does_not_retry_or_expose_body(self):
        http = session()
        http.request = Mock(return_value=response(status=401, body={"secret": "do-not-print"}))
        with patch.object(moomoo_rest.aiohttp, "ClientSession", return_value=http):
            with self.assertRaisesRegex(moomoo_rest.MoomooRestError, "HTTP 401") as error:
                await self.client.request("stock-basicinfo", {})
        self.assertNotIn("do-not-print", str(error.exception))
        http.request.assert_called_once()

    async def test_429_retries_bounded_and_long_cooldown_fails_fast(self):
        _, http = await self.request_with(response(status=429, headers={"Retry-After": "2"}), response(body={"ret_code": 0}))
        self.assertEqual(http.request.call_count, 2)
        with self.assertRaises(moomoo_rest.MoomooRestError):
            await self.request_with(response(status=429, headers={"Retry-After": "3600"}))
        with self.assertRaisesRegex(moomoo_rest.MoomooRestError, "cooldown"):
            await self.client.request("option-screen", {})

    async def test_write_endpoint_is_rejected(self):
        with self.assertRaisesRegex(moomoo_rest.MoomooRestError, "allowlist"):
            await self.client.request("order-place", {})

    async def test_empty_watchlist_never_becomes_full_market_scan(self):
        with patch.object(self.client, "request", new=AsyncMock()) as request:
            rows, coverage = await self.client.scan([])
        self.assertEqual(rows, [])
        self.assertTrue(coverage["complete"])
        request.assert_not_called()

    async def test_full_market_omits_whitelist_and_finds_new_ticker_after_excluded_page(self):
        def row(ticker):
            return {"code": f"US.{ticker}261016C00200000", "option_type": "CALL", "left_day": 7,
                    "volume": 500, "turnover": 90000}
        responses = [
            {"data": {"option_list": [row("SPY"), row("QQQ"), row("SPXW"), row("SPY1")]},
             "pagination": {"has_more": True, "next_key": "next", "total": 7}},
            {"data": {"option_list": [row("HUM"), row("BRK.B"), row("HUM")]},
             "pagination": {"has_more": False, "total": 7}},
        ]
        with patch.object(self.client, "request", new=AsyncMock(side_effect=responses)) as request, \
                patch.object(self.client, "profiles", new=AsyncMock()) as profiles:
            rows, coverage = await self.client.scan()
        profiles.assert_not_called()
        self.assertEqual([s["ticker"] for s in rows], ["HUM", "BRK.B"])
        self.assertEqual((coverage["excluded"], coverage["duplicates"], coverage["pages"]), (4, 1, 2))
        self.assertTrue(coverage["complete"])
        for call in request.call_args_list:
            strategy = call.args[1]["strategy"]
            self.assertEqual(strategy["market_category_list"], [0])
            self.assertFalse(any("underlying_list" in group for group in strategy["filter_group_list"]))

    async def test_excluded_single_ticker_never_calls_api(self):
        with patch.object(self.client, "request", new=AsyncMock()) as request:
            rows, _ = await self.client.scan(["QQQ", "SPY", "SPX"])
        self.assertEqual(rows, [])
        request.assert_not_called()

    async def test_adjusted_unknown_root_not_guessed_and_marks_coverage_partial(self):
        body = {"data": {"option_list": [{"code": "US.AAPL1261016C00200000"}]},
                "pagination": {"has_more": False}}
        with patch.object(self.client, "request", new=AsyncMock(return_value=body)):
            rows, coverage = await self.client.scan()
        self.assertEqual(rows, [])
        self.assertEqual(coverage["unresolved"], 1)
        self.assertFalse(coverage["complete"])

    async def test_deadline_preserves_completed_pages_but_never_reports_complete(self):
        first = {"data": {"option_list": []}, "pagination": {"has_more": True, "next_key": "next"}}
        with patch.object(self.client, "request", new=AsyncMock(side_effect=[first, asyncio.TimeoutError()])):
            rows, coverage = await self.client.scan()
        self.assertEqual(rows, [])
        self.assertEqual(coverage["stop_reason"], "timeout")
        self.assertFalse(coverage["complete"])
        with patch.object(self.client, "request", new=AsyncMock(side_effect=asyncio.TimeoutError())):
            with self.assertRaisesRegex(moomoo_rest.MoomooRestError, "deadline"):
                await self.client.scan()

    async def test_empty_whitelist_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            moomoo_rest.screen_payload([])

    async def test_health_empty_response_is_explicit_and_bad_shape_rejected(self):
        self.client._profiles["AAPL"] = {"stock_id": 123}
        with patch.object(self.client, "request", new=AsyncMock(return_value={"data": {"option_list": []}})):
            self.assertIn("Empty screen", await self.client.health())
        with patch.object(self.client, "request", new=AsyncMock(return_value={"data": {}})):
            with self.assertRaises(moomoo_rest.MoomooRestError):
                await self.client.health()

    async def test_metadata_batch_pagination_dedup_and_partial_flag(self):
        row = {"code": "US.AAPL261016C00200000", "option_type": "CALL", "left_day": 7,
               "volume": 200, "turnover": 50000, "underlying_info": {"stock_id": 123}}
        responses = [{"data": {"basic_list": [{"code": "US.AAPL", "stock_id": 123}]}},
                     {"data": {"option_list": [row]}, "pagination": {"has_more": True, "next_key": "opaque"}},
                     {"data": {"option_list": [row]}, "pagination": {"has_more": False, "next_key": "-1"}}]
        with patch.object(self.client, "request", new=AsyncMock(side_effect=responses)) as request:
            rows, coverage = await self.client.scan(["AAPL"])
        self.assertEqual(len(rows), 1)
        self.assertTrue(coverage["complete"])
        self.assertEqual(request.call_args_list[2].args[1]["next_key"], "opaque")
        with patch.object(config, "MOOMOO_MAX_PAGES", 1), \
                patch.object(self.client, "request", new=AsyncMock(return_value=responses[1])):
            _, coverage = await self.client.scan(["AAPL"])
        self.assertFalse(coverage["complete"])

    async def test_missing_metadata_or_schema_fails_not_unrestricted_scan(self):
        with patch.object(self.client, "request", new=AsyncMock(return_value={"data": {"basic_list": []}})) as request:
            with self.assertRaisesRegex(moomoo_rest.MoomooRestError, "metadata"):
                await self.client.scan(["MISSING"])
            request.assert_awaited_once()
        with self.assertRaisesRegex(moomoo_rest.MoomooRestError, "scoring fields"):
            self.client._validate_fields([moomoo_rest.normalize_option({"code": "US.AAPL261016C00200000"}, "AAPL", NOW.isoformat())])


class DatabaseTestCase(SettingsFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory
        for p in (patch.object(db, "_DB_PATH", Path(directory.name) / "bot.db"),
                  patch.object(tasks.Loop, "start"), patch.object(config, "OPTIONS_ALERT_CHANNEL_ID", 123),
                  patch.object(config, "CATALYST_ENABLED", False)):
            p.start()
            self.addCleanup(p.stop)
        options_store.init_db()
        catalyst_store.init_db()


class OptionsStorageTests(DatabaseTestCase):
    async def test_old_pending_excluded_events_are_retired_without_marking_sent(self):
        e = event(datetime.now(timezone.utc), ticker="SPY")
        options_store.save_event(e)
        self.assertEqual(options_store.pending_events(datetime.now(timezone.utc)), [])
        conn = db.get_connection()
        try:
            row = conn.execute("SELECT delivery, sent_at FROM option_events").fetchone()
            self.assertEqual(tuple(row), ("excluded", None))
        finally:
            conn.close()

    async def test_schema_upgrade_is_additive_and_repeatable(self):
        options_store.init_db()
        options_store.save_snapshots([snapshot()], "2026-10-09", 190)
        options_store.init_db()
        conn = db.get_connection()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM option_snapshots").fetchone()[0], 1)
            self.assertIsNotNone(conn.execute("SELECT name FROM sqlite_master WHERE name='option_alerts'").fetchone())
        finally:
            conn.close()

    async def test_same_clock_history_is_point_in_time_and_source_isolated(self):
        for offset in range(1, 7):
            time = NOW - timedelta(days=offset)
            options_store.save_snapshots([snapshot(observed_at=time.isoformat(), volume=1000)], time.date().isoformat(), 190)
            late = time + timedelta(hours=3)
            options_store.save_snapshots([snapshot(observed_at=late.isoformat(), volume=9000)], late.date().isoformat(), 190)
            options_store.save_snapshots([snapshot(observed_at=time.isoformat(), source="opend", volume=90000)], time.date().isoformat(), 190)
        future = NOW + timedelta(minutes=5)
        options_store.save_snapshots([snapshot(observed_at=future.isoformat())], NOW.date().isoformat(), 190)
        _, stats, previous = options_store.get_scoring_context("AAPL", NOW, "moomoo_rest")
        self.assertEqual(stats[snapshot()["contract_code"]], (1000, 0, 6))
        self.assertEqual(previous, {})

    async def test_baseline_matching_uses_et_across_dst(self):
        before = datetime(2026, 10, 30, 15, tzinfo=timezone.utc)
        after = datetime(2026, 11, 2, 16, tzinfo=timezone.utc)
        options_store.save_snapshots([snapshot(observed_at=before.isoformat())], before.date().isoformat(), 190)
        _, stats, _ = options_store.get_scoring_context("AAPL", after, "moomoo_rest")
        self.assertEqual(stats[snapshot()["contract_code"]][2], 1)

    async def test_pending_survives_restart_and_updates_strongest_payload(self):
        first = event(score=60, tier=2)
        key = options_store.save_event(first)
        newer = event(NOW + timedelta(minutes=5))
        self.assertEqual(options_store.save_event(newer), key)
        options_store.init_db()
        pending = options_store.pending_events(NOW + timedelta(minutes=6), 3)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["as_of"], newer["as_of"])
        self.assertEqual(options_store.pending_events(NOW), [])

    async def test_dedup_across_hour_boundary_and_unchanged_daily_totals(self):
        now = datetime.now(timezone.utc).replace(hour=15, minute=59, second=0, microsecond=0)
        first = event(now)
        key = options_store.save_event(first)
        options_store.complete_event(key)
        conn = db.get_connection()
        try:
            conn.execute("UPDATE option_events SET sent_at=?", (now.isoformat(),))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(options_store.save_event(event(now + timedelta(minutes=2))), key)
        self.assertEqual(options_store.save_event(event(now + timedelta(minutes=65))), key)
        changed = event(now + timedelta(minutes=65))
        changed["contracts"][0]["volume"] += 500
        self.assertNotEqual(options_store.save_event(changed), key)

    async def test_outcome_import_rejects_lookahead_in_features(self):
        key = options_store.save_event(event())
        with self.assertRaises(ValueError):
            options_store.record_outcome(key, NOW.isoformat(), {"return": 1})
        with self.assertRaises(ValueError):
            options_store.record_outcome(key, (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), {"return": .01})
        options_store.record_outcome(key, (NOW + timedelta(minutes=1)).isoformat(), {"return": .01})


class OptionsDeliveryTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.message = SimpleNamespace(edit=AsyncMock())
        self.channel = SimpleNamespace(send=AsyncMock(return_value=self.message))
        self.bot = Mock()
        self.bot.get_channel.return_value = self.channel
        self.cog = options_flow.OptionsFlow(self.bot)

    async def test_discord_failure_retains_pending_then_retries(self):
        e = event(datetime.now(timezone.utc))
        options_store.save_event(e)
        self.channel.send.side_effect = RuntimeError("Discord offline")
        with self.assertRaises(RuntimeError):
            await self.cog._post_event(e)
        self.assertEqual(len(options_store.pending_events(datetime.now(timezone.utc))), 1)
        self.channel.send.side_effect = None
        await self.cog._post_event(e)
        self.assertEqual(options_store.pending_events(datetime.now(timezone.utc)), [])

    async def test_llm_failure_and_timeout_keep_original_quantitative_alert(self):
        e = event(datetime.now(timezone.utc))
        options_store.save_event(e)
        async def fail(*args):
            self.channel.send.assert_awaited_once()
            raise asyncio.TimeoutError()
        with patch.object(config, "CATALYST_ENABLED", True), \
                patch.object(options_flow.catalyst_intelligence, "investigate", new=fail):
            await self.cog._post_event(e)
        embed = self.message.edit.call_args.kwargs["embed"]
        self.assertIn("AI/search unavailable", embed.fields[-1].value)
        self.assertIn("daily activity", embed.description)
        self.assertEqual(options_store.pending_events(datetime.now(timezone.utc)), [])

    async def test_scan_snapshot_database_score_and_delivery_chain(self):
        with patch.object(self.cog, "_collect", new=AsyncMock(return_value=([snapshot()], {"complete": False, "profiles": {}}))):
            events, coverage, count = await self.cog._scan(["AAPL"])
        self.assertEqual((len(events), count, coverage["complete"]), (1, 1, False))
        self.assertEqual(len(options_store.pending_events(datetime.now(timezone.utc))), 1)
        await self.cog._post_digest()
        self.channel.send.assert_awaited_once()
        self.assertEqual(options_store.pending_events(datetime.now(timezone.utc)), [])

    async def test_market_scan_groups_returned_symbols_and_filters_backend_leaks(self):
        rows = [snapshot(ticker="HUM", contract_code="US.HUM261016C00200000"), snapshot(ticker="QQQ")]
        with patch.object(self.cog, "_collect", new=AsyncMock(return_value=(rows, {"complete": True, "profiles": {}}))):
            events, _, _ = await self.cog._scan(None)
        self.assertEqual([e["ticker"] for e in events], ["HUM"])
        self.assertTrue(all(e["ticker"] == "HUM" for e in options_store.pending_events(datetime.now(timezone.utc))))

    async def test_all_base_alerts_sent_before_bounded_concurrent_research(self):
        now = datetime.now(timezone.utc)
        for ticker in ("AAPL", "HUM", "NVDA", "AMD"):
            options_store.save_event(event(now, ticker=ticker))
        active, peak = 0, 0
        async def research(e, profile=None):
            nonlocal active, peak
            self.assertEqual(self.channel.send.await_count, 4)
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"status": "complete", "research": {"summary": "fixture"}}
        with patch.object(config, "CATALYST_ENABLED", True), \
                patch.object(self.cog, "_scan", new=AsyncMock(return_value=([], {"profiles": {}}, 0))) as scan, \
                patch.object(self.cog._rest, "profiles", new=AsyncMock(return_value={})), \
                patch.object(options_flow.catalyst_intelligence, "investigate", new=research):
            await self.cog._run_scan()
        scan.assert_awaited_once_with(None)
        self.assertEqual(peak, config.CATALYST_MAX_CONCURRENCY)
        self.assertEqual(self.message.edit.await_count, 4)

    async def test_excluded_event_and_preview_do_not_send_alert_or_query(self):
        await self.cog._post_event(event(datetime.now(timezone.utc), ticker="SPY"))
        self.channel.send.assert_not_called()
        ctx = SimpleNamespace(send=AsyncMock())
        with patch.object(config, "MOOMOO_ENABLED", True), patch.object(self.cog, "_scan", new=AsyncMock()) as scan:
            await self.cog._preview(ctx, "QQQ")
        scan.assert_not_called()
        self.assertIn("excluded", ctx.send.call_args.args[0])

    async def test_manual_research_uses_real_l2_candidate_without_persisting_alert(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def typing():
            yield

        ctx = SimpleNamespace(send=AsyncMock(return_value=self.message), typing=typing)
        e = event(datetime.now(timezone.utc), tier=2, score=55)
        with patch.object(config, "MOOMOO_ENABLED", True), patch.object(config, "CATALYST_ENABLED", True), \
                patch.object(options_flow, "_within_market_hours", return_value=True), \
                patch.object(self.cog, "_scan", new=AsyncMock(return_value=([e], {"profiles": {}}, 1))) as scan, \
                patch.object(options_flow.catalyst_intelligence, "investigate", new=AsyncMock(
                    return_value={"status": "complete", "research": {"summary": "fixture"}})) as research:
            await self.cog.cmd_option_research.callback(self.cog, ctx, "AAPL")
        scan.assert_awaited_once_with(["AAPL"], persist=False)
        research.assert_awaited_once()
        self.assertTrue(any(f.name == "Manual Research Preview" for f in self.message.edit.call_args.kwargs["embed"].fields))
        self.assertEqual(options_store.pending_events(datetime.now(timezone.utc)), [])

    async def test_manual_research_refuses_closed_session(self):
        ctx = SimpleNamespace(send=AsyncMock())
        with patch.object(config, "MOOMOO_ENABLED", True), patch.object(config, "CATALYST_ENABLED", True), \
                patch.object(options_flow, "_within_market_hours", return_value=False), \
                patch.object(self.cog, "_scan", new=AsyncMock()) as scan, \
                patch.object(options_flow.catalyst_intelligence, "investigate", new=AsyncMock()) as research:
            await self.cog.cmd_option_research.callback(self.cog, ctx, "AAPL")
        scan.assert_not_called()
        research.assert_not_called()

    async def test_overflow_remains_pending_for_digest(self):
        now = datetime.now(timezone.utc)
        for i in range(6):
            options_store.save_event(event(now, ticker=f"TEST{i}"))
        with patch.object(self.cog, "_scan", new=AsyncMock(return_value=([], {"profiles": {}}, 0))), \
                patch.object(config, "OPTIONS_MAX_ALERTS_PER_SCAN", 2):
            await self.cog._run_scan()
        self.assertEqual(len(options_store.pending_events(datetime.now(timezone.utc))), 4)
        await self.cog._post_digest()
        self.assertEqual(options_store.pending_events(datetime.now(timezone.utc)), [])

    async def test_digest_send_failure_retains_pending(self):
        options_store.save_event(event(datetime.now(timezone.utc), tier=2))
        self.channel.send.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            await self.cog._post_digest()
        self.assertEqual(len(options_store.pending_events(datetime.now(timezone.utc))), 1)

    async def test_early_close_and_holidays_stop_scans(self):
        for stamp, expected in (("2026-11-27T17:59:00+00:00", True),
                                ("2026-11-27T18:01:00+00:00", False),
                                ("2026-11-26T17:00:00+00:00", False)):
            now = datetime.fromisoformat(stamp).astimezone(ET)
            with patch.object(options_flow, "market_now", return_value=now), \
                    patch.object(scheduler, "market_today", return_value=now.date()):
                self.assertEqual(options_flow._within_market_hours(), expected)

    async def test_embeds_within_discord_limits_and_do_not_claim_trade_premium(self):
        e = event()
        e["contracts"] *= 3
        embed = options_flow.event_embed(e)
        options_flow.add_catalyst(embed, {"research": {"summary": "x" * 5000},
            "sources": [{"source": "web", "url": "https://example.test/" + "x" * 700} for _ in range(3)]})
        self.assertLessEqual(len(embed), 6000)
        self.assertTrue(all(len(f.value) <= 1024 and len(f.name) <= 256 for f in embed.fields))
        self.assertIn("not a block trade", embed.footer.text)
        self.assertIn("unknown", embed.description)
