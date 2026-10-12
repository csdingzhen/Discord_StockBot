"""Real cogs + temporary SQLite + Discord embeds; HTTP/Discord edges are mocked."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord
from discord.ext import commands, tasks

import config
from cogs import analysis, news, scheduler
from services import llm_client, premarket_data
from services.jin10_mcp import Jin10Error
from storage import db, jin10_store
from tests.http_fakes import response, session


def item(n, content=None):
    return {"url": f"https://example.test/flash/{n}",
            "time": f"2026-10-09 12:{n:02}:00", "content": content or f"News event {n}"}


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        patches = [patch.object(db, "_DB_PATH", Path(directory.name) / "bot.db"),
                   patch.object(tasks.Loop, "start"),
                   patch.object(config, "MARKET_CHANNEL_ID", 1),
                   patch.object(config, "NEWS_CHANNEL_ID", 2),
                   patch.object(config, "ALERT_CHANNEL_ID", 3),
                   patch.object(config, "JIN10_API_KEY", "test-token"),
                   patch.object(config, "OPENROUTER_API_KEY", "test-token"),
                   patch.object(llm_client, "_cooldown_until", 0)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.market = SimpleNamespace(send=AsyncMock())
        self.news_channel = SimpleNamespace(send=AsyncMock())
        self.alerts = SimpleNamespace(send=AsyncMock())
        self.bot = Mock()
        self.bot.get_channel.side_effect = {1: self.market, 2: self.news_channel, 3: self.alerts}.get
        self.news = news.News(self.bot)
        self.scheduler = scheduler.Scheduler(self.bot)

    async def poll(self, items=None, error=None):
        with patch.object(news, "Jin10MCPClient", return_value=MagicMock()), \
                patch.object(self.news, "_fetch_new_items", new=AsyncMock(return_value=items or [], side_effect=error)):
            await self.news.poll_flash.coro(self.news)

    async def test_premarket_credit_failure_still_sends_basic_data(self):
        snapshot = {"VIX": {"price": 20, "pct_change": 1}, "Gold/Oil Ratio": {"value": 30}}
        http = session(response(status=402))
        with patch.object(scheduler, "fetch_premarket_snapshot", return_value=snapshot), \
                patch.object(llm_client.aiohttp, "ClientSession", return_value=http):
            await self.scheduler._send_premarket_brief()
        embed = self.market.send.call_args.kwargs["embed"]
        self.assertIn("20.00", embed.description)
        self.assertNotIn("AI分析", embed.description)
        self.assertIn(llm_client.FAILURE_NOTICE, embed.footer.text)

    async def test_premarket_success_and_missing_key(self):
        snapshot = {"VIX": {"price": 20, "pct_change": None}}
        with patch.object(scheduler, "fetch_premarket_snapshot", return_value=snapshot), \
                patch.object(scheduler, "analyze_premarket", new=AsyncMock(return_value="analysis")):
            await self.scheduler._send_premarket_brief()
        self.assertIn("analysis", self.market.send.call_args.kwargs["embed"].description)
        with patch.object(scheduler, "fetch_premarket_snapshot", return_value=snapshot), \
                patch.object(config, "OPENROUTER_API_KEY", ""):
            await self.scheduler._send_premarket_brief()
        self.assertIn(llm_client.FAILURE_NOTICE, self.market.send.call_args.kwargs["embed"].footer.text)

    async def test_scheduled_failure_does_not_kill_next_run(self):
        job = AsyncMock(side_effect=[RuntimeError("delivery failed"), None])
        await scheduler._run_scheduled("test", job)
        await scheduler._run_scheduled("test", job)
        self.assertEqual(job.await_count, 2)

    async def test_cancellation_is_not_swallowed(self):
        with self.assertRaises(asyncio.CancelledError):
            await scheduler._run_scheduled("cancel", AsyncMock(side_effect=asyncio.CancelledError))

    def test_single_quote_failure_keeps_other_indicators(self):
        def quote(symbol):
            if symbol == "bad":
                raise RuntimeError("quote unavailable")
            return {"price": 100, "prev_close": 90}
        with patch.object(premarket_data, "PREMARKET_TICKERS", {"Gold": "good", "Brent Crude": "bad"}), \
                patch.object(premarket_data, "_fetch_quote", side_effect=quote):
            data = premarket_data.fetch_premarket_snapshot()
        self.assertEqual(data["Gold"]["price"], 100)
        self.assertIsNone(data["Brent Crude"]["price"])
        self.assertIsNone(data["Gold/Oil Ratio"]["value"])

    async def test_daily_summary_cnn_failure_does_not_block_indices(self):
        with patch.object(scheduler, "INDICES", {"S&P 500": "^GSPC"}), \
                patch.object(scheduler, "get_ticker_info", return_value={"regularMarketPrice": 100, "previousClose": 90}), \
                patch.object(scheduler.requests, "get", side_effect=RuntimeError("offline")):
            await self.scheduler._send_summary()
        embed = self.market.send.call_args.kwargs["embed"]
        self.assertIn("100.00", embed.description)
        self.assertIn("unavailable", embed.description)

    async def test_llm_outage_queues_raw_news_without_jin10_outage_alert(self):
        roundup = item(3, "每日科技要闻速递 test roundup")
        with patch.object(llm_client, "classify_flash", new=AsyncMock(side_effect=llm_client.LLMError("credits"))) as classify:
            await self.poll([item(1), item(2), roundup])
        self.assertEqual(classify.await_count, 1)
        self.assertEqual(len(jin10_store.get_pending_l2_items()), 2)
        self.assertEqual(self.news._consecutive_failures, 0)
        self.alerts.send.assert_not_called()
        self.assertIn("金十汇总", self.news_channel.send.call_args.kwargs["embed"].author.name)
        with patch.object(llm_client, "summarize_flash_digest", new=AsyncMock(side_effect=llm_client.LLMError("credits"))):
            await self.news._post_l2_digest()
        embed = self.news_channel.send.call_args.kwargs["embed"]
        self.assertIn("非 AI", embed.title)
        self.assertIn("News event 1", embed.description)
        self.assertIn(llm_client.FAILURE_NOTICE, embed.footer.text)
        self.assertEqual(jin10_store.get_pending_l2_items(), [])

    async def test_successful_classification_routes_all_three_levels(self):
        classifications = [{"level": level, "category": "AI"} for level in (1, 2, 3)]
        with patch.object(llm_client, "classify_flash", new=AsyncMock(side_effect=classifications)), \
                patch.object(llm_client, "summarize_flash_alert", new=AsyncMock(return_value="analysis")):
            await self.poll([item(1), item(2), item(3)])
        self.assertEqual(len(jin10_store.get_recent_items()), 3)
        self.assertEqual([row["url"] for row in jin10_store.get_pending_l2_items()], [item(2)["url"]])
        self.alerts.send.assert_awaited_once()
        embed = self.alerts.send.call_args.kwargs["embed"]
        self.assertIn("News event 3", embed.description)
        self.assertEqual(embed.fields[0].value, "analysis")
        self.news_channel.send.assert_not_called()

    async def test_fetch_outage_warns_once_in_alerts_and_reports_recovery(self):
        for _ in range(6):
            await self.poll(error=Jin10Error("initialize: HTTP 401"))
        self.assertEqual(self.alerts.send.await_count, 1)
        self.news_channel.send.assert_not_called()
        embed = self.alerts.send.call_args.kwargs["embed"]
        self.assertIn("HTTP 401", embed.description)
        self.assertIn("不包含 LLM", embed.description)
        await self.poll([])
        self.assertEqual(self.alerts.send.await_count, 2)
        self.assertIn("已恢复", self.alerts.send.call_args.kwargs["embed"].title)
        self.assertEqual(self.news._consecutive_failures, 0)

    async def test_failed_roundup_send_does_not_mark_it_seen(self):
        self.news_channel.send.side_effect = RuntimeError("Discord unavailable")
        await self.poll([item(1, "每日要闻 test")])
        self.assertFalse(jin10_store.is_seen(item(1)["url"]))

    async def test_failed_digest_send_keeps_rows_pending(self):
        jin10_store.save_classified_item(item(1), 2, "AI")
        self.news_channel.send.side_effect = RuntimeError("Discord unavailable")
        with patch.object(llm_client, "summarize_flash_digest", new=AsyncMock(side_effect=llm_client.LLMError("credits"))):
            with self.assertRaises(RuntimeError):
                await self.news._post_l2_digest()
        self.assertEqual(len(jin10_store.get_pending_l2_items()), 1)

    async def test_pending_l3_retries_delivery_without_new_items(self):
        jin10_store.save_classified_item(item(1), 3, "AI")
        self.alerts.send.side_effect = RuntimeError("Discord unavailable")
        with patch.object(llm_client, "summarize_flash_alert", new=AsyncMock(side_effect=llm_client.LLMError("credits"))):
            await self.poll([])
            self.assertEqual(len(jin10_store.get_pending_l3_items()), 1)
            self.alerts.send.side_effect = None
            await self.poll([])
        self.assertEqual(jin10_store.get_pending_l3_items(), [])
        embed = self.alerts.send.call_args.kwargs["embed"]
        self.assertIn(llm_client.FAILURE_NOTICE, embed.footer.text)

    def test_old_unposted_l3_records_are_not_replayed(self):
        jin10_store.save_classified_item(item(1), 3, "AI")
        conn = db.get_connection()
        try:
            conn.execute("UPDATE jin10_flash SET fetched_at = ?", ("2000-01-01T00:00:00+00:00",))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(jin10_store.get_pending_l3_items(), [])

    async def test_successful_digest_marks_rows_and_labels_unclassified_items(self):
        jin10_store.save_classified_item(item(1), 2, news._UNCLASSIFIED)
        with patch.object(llm_client, "summarize_flash_digest", new=AsyncMock(return_value="AI digest")):
            await self.news._post_l2_digest()
        embed = self.news_channel.send.call_args.kwargs["embed"]
        self.assertEqual(embed.description, "AI digest")
        self.assertIn("未AI分级", embed.footer.text)
        self.assertEqual(jin10_store.get_pending_l2_items(), [])

    async def test_raw_digest_paginates_within_discord_limits(self):
        for i in range(30):
            jin10_store.save_classified_item(item(i, "x" * 3000), 2, news._UNCLASSIFIED)
        with patch.object(llm_client, "summarize_flash_digest", new=AsyncMock(side_effect=llm_client.LLMError("credits"))):
            await self.news._post_l2_digest()
        self.assertGreater(self.news_channel.send.await_count, 1)
        for call in self.news_channel.send.call_args_list:
            embed = call.kwargs["embed"]
            self.assertLessEqual(len(embed.description), 4096)
            self.assertLessEqual(len(embed), 6000)
        self.assertEqual(jin10_store.get_pending_l2_items(), [])

    async def test_pagination_processes_unseen_items_after_seen_in_page(self):
        jin10_store.save_classified_item(item(2), 1, "AI")
        client = SimpleNamespace(list_flash=AsyncMock(return_value={
            "items": [item(3), item(2), item(1)], "has_more": True, "next_cursor": "old",
        }))
        results = await self.news._fetch_new_items(client)
        self.assertEqual([r["url"] for r in results], [item(1)["url"], item(3)["url"]])
        client.list_flash.assert_awaited_once()

    async def test_analyze_command_returns_quotes_on_llm_failure(self):
        ctx = MagicMock()
        ctx.send = AsyncMock()
        with patch.object(analysis, "get_ticker_info", return_value={"currentPrice": 100}), \
                patch.object(analysis, "analyze_ticker", new=AsyncMock(side_effect=llm_client.LLMError("credits"))):
            await analysis.Analysis.cmd_analyze.callback(analysis.Analysis(self.bot), ctx, "AAPL")
        embed = ctx.send.call_args.kwargs["embed"]
        self.assertIn("100", embed.description)
        self.assertIn(llm_client.FAILURE_NOTICE, embed.footer.text)

    async def test_all_extensions_load_and_register_commands_offline(self):
        from bot import COGS

        bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        async with bot:
            for extension in COGS:
                await bot.load_extension(extension)
            self.assertEqual(set(bot.extensions), set(COGS))
            for name in ("premarket", "earnings", "newsdigest", "analyze", "weeklyearnings",
                         "options", "optionhealth", "optionscan", "optionflow", "optionresearch"):
                self.assertIsNotNone(bot.get_command(name))
