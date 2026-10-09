"""Offline regression tests: no Discord login, API calls, or production DB writes."""
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cogs import news, options_flow, scheduler
from services import earnings_data, moomoo_client, sec_filings
from utils import market_time
from utils.formatters import make_embed


class MarketClockTests(unittest.TestCase):
    def test_date_boundaries_and_dst(self):
        cases = [
            ("2026-07-07T00:30:00+00:00", "2026-07-06", -4, 20, 0),
            ("2026-01-06T00:30:00+00:00", "2026-01-05", -5, 19, 0),
            ("2026-07-06T21:30:00-07:00", "2026-07-07", -4, 0, 0),
            ("2026-03-08T06:59:00+00:00", "2026-03-08", -5, 1, 0),
            ("2026-03-08T07:00:00+00:00", "2026-03-08", -4, 3, 0),
            ("2026-11-01T05:30:00+00:00", "2026-11-01", -4, 1, 0),
            ("2026-11-01T06:30:00+00:00", "2026-11-01", -5, 1, 1),
        ]
        for instant, day, offset, hour, fold in cases:
            with self.subTest(instant=instant), patch.object(market_time, "datetime") as clock:
                clock.now.side_effect = lambda tz: datetime.fromisoformat(instant).astimezone(tz)
                now = market_time.market_now()
                self.assertEqual(market_time.market_today().isoformat(), day)
                self.assertEqual(now.utcoffset(), timedelta(hours=offset))
                self.assertEqual((now.hour, now.fold), (hour, fold))
                clock.now.assert_called_with(market_time.ET)

    def test_embed_timestamp_is_aware_utc(self):
        self.assertEqual(make_embed("Test").timestamp.utcoffset(), timedelta(0))


class MarketDateConsumersTests(unittest.TestCase):
    def setUp(self):
        # UTC has already reached Tuesday; New York is still on Monday.
        clock_patch = patch.object(market_time, "datetime")
        clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        clock.now.side_effect = lambda tz: datetime(2026, 7, 7, 0, 30, tzinfo=timezone.utc).astimezone(tz)

    def test_nyse_uses_new_york_date(self):
        calendar = Mock()
        calendar.schedule.return_value.empty = True
        with patch.object(scheduler.mcal, "get_calendar", return_value=calendar):
            self.assertIsNone(scheduler._nyse_schedule_today())
        calendar.schedule.assert_called_once_with(start_date="2026-07-06", end_date="2026-07-06")

    def test_week_bounds_do_not_advance_on_utc_monday(self):
        sunday = datetime(2026, 7, 6, 0, 30, tzinfo=timezone.utc)
        with patch.object(market_time, "market_now", return_value=sunday.astimezone(market_time.ET)):
            self.assertEqual(earnings_data._week_bounds(), ("2026-06-29", "2026-07-03"))

    def test_market_calendar_preserves_holidays_and_early_close(self):
        for day, is_open, early in [(date(2026, 11, 26), False, False),
                                    (date(2026, 11, 27), True, True),
                                    (date(2026, 11, 30), True, False)]:
            with self.subTest(day=day), patch.object(scheduler, "market_today", return_value=day):
                self.assertEqual(scheduler.market_open_today(), is_open)
                self.assertEqual(scheduler.is_early_close_today(), early)

    def test_options_market_hours_use_et_in_both_seasons(self):
        for instant, within_hours in [("2026-07-06T13:29:00+00:00", False),
                                      ("2026-07-06T13:30:00+00:00", True),
                                      ("2026-01-05T14:30:00+00:00", True)]:
            now = datetime.fromisoformat(instant).astimezone(market_time.ET)
            with self.subTest(instant=instant), patch.object(options_flow, "market_now", return_value=now):
                self.assertEqual(options_flow._within_market_hours(), within_hours)

    def test_weekly_sources_use_same_et_week(self):
        with patch.object(earnings_data, "_fmp_get", return_value=[]) as fmp, \
                patch.object(earnings_data, "_fetch_nasdaq_calendar_day", return_value=[]) as nasdaq:
            self.assertEqual(earnings_data.fetch_weekly_calendar(), [])
        fmp.assert_called_once_with("/earnings-calendar", {"from": "2026-07-06", "to": "2026-07-10"})
        self.assertEqual([c.args[0] for c in nasdaq.call_args_list], [f"2026-07-{d:02}" for d in range(6, 11)])

    def test_daily_results_use_et_date(self):
        with patch.object(earnings_data, "_fmp_get", return_value=[]) as fmp, \
                patch.object(earnings_data, "_fetch_nasdaq_calendar_day", return_value=[]) as nasdaq:
            self.assertEqual(earnings_data.fetch_todays_results(), [])
        fmp.assert_called_once_with("/earnings-calendar", {"from": "2026-07-06", "to": "2026-07-06"})
        nasdaq.assert_called_once_with("2026-07-06")

    def test_upcoming_window_starts_on_et_date(self):
        with patch.object(earnings_data, "_fmp_get", return_value=[]) as fmp:
            earnings_data._fetch_upcoming_from_fmp("AAPL")
        fmp.assert_called_once_with("/earnings-calendar", {
            "from": "2026-07-06",
            "to": (date(2026, 7, 6) + timedelta(days=95)).isoformat(),
        })

    def test_sec_lookback_includes_et_boundary(self):
        response = Mock()
        response.json.return_value = {"filings": {"recent": {
            "form": ["8-K"], "items": ["2.02"], "filingDate": ["2026-07-04"],
            "accessionNumber": ["test-id"], "primaryDocument": ["test.htm"],
        }}}
        with patch.object(sec_filings, "get_cik_for_ticker", return_value="0000000001"), \
                patch.object(sec_filings.requests, "get", return_value=response), \
                patch.object(sec_filings.sec_store, "is_seen", return_value=False), \
                patch.object(sec_filings.sec_store, "save_filing") as save, \
                patch.object(sec_filings.time, "sleep"):
            self.assertEqual(len(sec_filings.fetch_new_earnings_filings({"AAPL"})), 1)
        self.assertEqual(save.call_args.args[0]["filing_date"], "2026-07-04")

    def test_options_horizon_uses_et_date(self):
        from utils.constants import OPTIONS_CHAIN_DTE_MAX

        horizon = (date(2026, 7, 6) + timedelta(days=OPTIONS_CHAIN_DTE_MAX)).isoformat()
        too_late = (date.fromisoformat(horizon) + timedelta(days=1)).isoformat()
        expirations = {"strike_time": SimpleNamespace(values=SimpleNamespace(tolist=lambda: [horizon, too_late]))}
        ctx = Mock()
        ctx.get_option_expiration_date.return_value = (0, expirations)
        ctx.get_option_chain.return_value = (1, None)
        with patch.object(moomoo_client, "_load_moomoo", return_value=Mock(RET_OK=0)), \
                patch.object(moomoo_client, "_QuoteContext") as context:
            context.return_value.__enter__.return_value = ctx
            moomoo_client.build_universe("AAPL")
        self.assertEqual(ctx.get_option_chain.call_count, 2)
        self.assertTrue(all(c.kwargs["end"] == horizon for c in ctx.get_option_chain.call_args_list))


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_monday_gate_uses_et_weekday(self):
        cog = SimpleNamespace(_send_weekly_earnings=AsyncMock())
        with patch.object(scheduler, "market_open_today", return_value=True):
            for instant, count in [("2026-07-06T00:30:00+00:00", 0), ("2026-07-07T00:30:00+00:00", 1)]:
                with patch.object(market_time, "market_now", return_value=datetime.fromisoformat(instant).astimezone(market_time.ET)):
                    await scheduler.Scheduler.weekly_earnings_update.coro(cog)
                self.assertEqual(cog._send_weekly_earnings.await_count, count)

    async def test_options_cache_and_digest_use_et_date(self):
        cog = options_flow.OptionsFlow.__new__(options_flow.OptionsFlow)
        cog._universe = {"AAPL": ["cached-contract"]}
        cog._universe_date = "2026-07-06"
        now = datetime(2026, 7, 7, 0, 30, tzinfo=timezone.utc).astimezone(market_time.ET)
        with patch.object(market_time, "market_now", return_value=now), \
                patch.object(moomoo_client, "build_universe") as build, \
                patch.object(options_flow.options_store, "get_pending_digest", return_value=[]) as pending:
            self.assertEqual(await cog._get_universe("AAPL"), ["cached-contract"])
            await cog._post_digest()
        build.assert_not_called()
        pending.assert_called_once_with("2026-07-06")

    async def test_actual_discord_loops_follow_dst(self):
        loops = [
            (scheduler.Scheduler.premarket_update, 9, 0),
            (scheduler.Scheduler.weekly_earnings_update, 9, 0),
            (scheduler.Scheduler.normal_close_update, 16, 5),
            (scheduler.Scheduler.early_close_update, 13, 5),
            (scheduler.Scheduler.aftermarket_earnings_update, 17, 30),
        ]
        for day, offset in [("2026-03-06", 5), ("2026-03-09", 4), ("2026-10-30", 4), ("2026-11-02", 5)]:
            for loop, hour, minute in loops:
                with self.subTest(day=day, hour=hour, minute=minute):
                    # Exercise discord.py's actual next-trigger calculation without starting tasks.
                    now = datetime.fromisoformat(day + "T12:00:00+00:00")
                    trigger = loop._get_next_sleep_time(now)
                    self.assertEqual((trigger.hour, trigger.minute), (hour, minute))
                    self.assertEqual(trigger.astimezone(timezone.utc).hour, hour + offset)
                    self.assertEqual(trigger.date().isoformat(), day)

    async def test_all_wall_clock_loops_share_timezone(self):
        self.assertIs(news.ET, market_time.ET)
        self.assertIs(options_flow.ET, market_time.ET)
        for cls in (scheduler.Scheduler, options_flow.OptionsFlow, news.News):
            for value in vars(cls).values():
                if isinstance(value, scheduler.tasks.Loop) and value.time:
                    for trigger in value.time:
                        self.assertIs(trigger.tzinfo, market_time.ET)


if __name__ == "__main__":
    unittest.main()
