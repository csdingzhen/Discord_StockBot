"""Existing options commands and channel, now backed by ticker-level events."""
import asyncio
import logging
import re
import time as clock
from collections import defaultdict
from datetime import datetime, time, timezone

import discord
from discord.ext import commands, tasks

import config
from cogs.scheduler import _nyse_schedule_today, market_open_today
from services import catalyst_intelligence, moomoo_client, options_scan
from services.metrics import options_pipeline_total, options_scan_duration_seconds
from services.moomoo_rest import MoomooRestClient
from storage import catalyst_store, options_store
from utils.market_time import ET, market_now, market_today

_log = logging.getLogger(__name__)
_DIGEST_TIMES = [time(11, 30, tzinfo=ET), time(13, 30, tzinfo=ET),
                 time(15, 30, tzinfo=ET), time(16, 15, tzinfo=ET)]


def _within_market_hours():
    row = _nyse_schedule_today()
    return row is not None and row["market_open"] <= market_now() <= row["market_close"]


def _alert_channel(bot):
    return bot.get_channel(config.OPTIONS_ALERT_CHANNEL_ID) if config.OPTIONS_ALERT_CHANNEL_ID else None


def event_embed(event):
    embed = discord.Embed(title=f"Unusual Options | {event['ticker']}", color=discord.Color.orange(),
                          timestamp=datetime.fromisoformat(event["as_of"]))
    embed.description = (f"**Unusual Score: {event['score']}/100**\n"
        f"Direction: {event['directional_bias']} | evidence strength: {event['direction_confidence']}/100\n"
        f"Multi-leg risk: {event['multi_leg_risk']}\n"
        f"Selected contracts' daily activity: **${event['premium']:,.0f}**")
    for contract in event["contracts"][:3]:
        ratio = contract.get("vol_oi_ratio")
        oi = contract.get("open_interest")
        oi_text = f"{oi:,.0f}" if oi is not None else "unknown"
        reasons = ", ".join(options_scan.reason_labels(contract["reasons"]))
        value = (f"Volume {contract['volume']:,.0f} / OI {oi_text}"
                 + (f" ({ratio:.1f}x)" if ratio is not None else "")
                 + f"\nDaily premium: ${contract['notional']:,.0f} ({contract['premium_basis']})"
                 + f"\nDTE {contract['dte']:g} | History {contract['history_days']} days\n{reasons}")
        if contract.get("iv") is not None:
            value += f"\nIV {contract['iv']:.1f}%"
        if contract.get("interval_volume") is not None:
            value += f" | Interval volume +{contract['interval_volume']:,.0f}"
        value += f"\nAggressive buying: {contract['aggressive_buying']}"
        value += " | Repeated buying: " + ("inferred" if contract["repeated_buying"] else "not established")
        embed.add_field(name=options_scan.contract_label(contract)[:100], value=value[:850], inline=False)
    if not event.get("coverage_complete", True):
        embed.add_field(name="Coverage", value="Partial candidate set; not a full-market total.", inline=False)
    embed.set_footer(text="Aggregate snapshots, not execution tape. Daily premium is not a block trade. "
                     "OI opening activity and exchange quote age may be unknown. Scores are not probabilities.")
    return embed


def add_catalyst(embed, result):
    score = result.get("evidence_score")
    text = (f"Classification: {result.get('classification', 'unavailable')}\n"
            f"Catalyst Evidence Score: {str(score) + '/100' if score is not None else 'N/A'}\n"
            + result.get("research", {}).get("summary", "Research unavailable."))
    embed.add_field(name="Potential Catalyst", value=text[:1024], inline=False)
    links = []
    for source in result.get("sources", [])[:3]:
        url = catalyst_store.safe_url(source.get("url"))
        if url:
            stamp = source.get("published_at") or "publication time unverified"
            line = f"[{source.get('source', 'Source')}]({url}) | {stamp}"
            if len("\n".join([*links, line])) <= 1024:
                links.append(line)
    if links:
        embed.add_field(name="Evidence Sources", value="\n".join(links), inline=False)
    return embed


class OptionsFlow(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        options_store.init_db()
        catalyst_store.init_db()
        self._rest = MoomooRestClient()
        self._scan_lock = asyncio.Lock()
        self._research_slots = asyncio.Semaphore(config.CATALYST_MAX_CONCURRENCY)
        self._consecutive_failures = 0
        self._failure_alerted = False
        self._universe = {}
        self._universe_date = None
        self._pruned_date = None
        self.scan_loop.start()
        self.flush_digest.start()

    def cog_unload(self):
        self.scan_loop.cancel()
        self.flush_digest.cancel()

    async def _get_universe(self, ticker):
        today = market_today().isoformat()
        if self._universe_date != today:
            self._universe, self._universe_date = {}, today
        if ticker not in self._universe:
            codes = await asyncio.to_thread(moomoo_client.build_universe, ticker)
            if codes:
                self._universe[ticker] = codes
            return codes
        return self._universe[ticker]

    async def _collect(self, tickers):
        if config.MOOMOO_TRANSPORT == "rest":
            return await self._rest.scan(tickers)
        if config.MOOMOO_TRANSPORT != "opend":
            raise ValueError("MOOMOO_TRANSPORT must be rest or opend")
        if tickers is None:
            raise ValueError("Market-wide screening requires the REST backend")
        snapshots, successes = [], 0
        for ticker in tickers:
            try:
                codes = await self._get_universe(ticker)
                if not codes:
                    continue
                spot, rows = await asyncio.to_thread(moomoo_client.fetch_ticker_snapshots, ticker, codes)
                successes += 1
                observed = datetime.now(timezone.utc).isoformat()
                snapshots.extend({**s, "source": "opend", "observed_at": observed,
                                  "underlying_price": spot, "quote_time": None} for s in rows)
            except Exception:
                _log.exception("OpenD scan failed for %s", ticker)
            await asyncio.sleep(config.MOOMOO_REQUEST_INTERVAL)
        if not successes:
            raise RuntimeError("No successful OpenD option snapshots")
        return snapshots, {"complete": successes == len(tickers), "profiles": {}}

    async def _scan(self, tickers=None, persist=True):
        snapshots, coverage = await self._collect(tickers)
        as_of = datetime.now(timezone.utc)
        if persist and self._pruned_date != as_of.date():
            await asyncio.to_thread(options_store.prune_snapshots, as_of)
            self._pruned_date = as_of.date()
        anomalies, grouped = [], defaultdict(list)
        for snap in snapshots:
            if not options_scan.excluded_underlying(snap["ticker"]):
                grouped[snap["ticker"]].append(snap)
        for ticker, rows in grouped.items():
            prior_iv, baselines, previous = await asyncio.to_thread(
                options_store.get_scoring_context, ticker, as_of, rows[0]["source"])
            anomalies.extend(options_scan.detect_anomalies(rows, prior_iv, baselines,
                                                           as_of=as_of, previous=previous))
            if persist:
                await asyncio.to_thread(options_store.save_snapshots, rows,
                    as_of.astimezone(ET).date().isoformat(), rows[0].get("underlying_price"))
        events = options_scan.aggregate_events(anomalies, as_of)
        options_pipeline_total.labels(stage="contracts", status="scanned").inc(len(snapshots))
        options_pipeline_total.labels(stage="events", status="detected").inc(len(events))
        if not coverage["complete"]:
            _log.warning("Options scan coverage is partial (page cap or missing backend data)")
        _log.info("Options scan scope=%s pages=%s received=%s excluded=%s unresolved=%s "
                  "tickers=%s events=%s complete=%s stop=%s collection_seconds=%s",
                  coverage.get("scope", "watchlist"), coverage.get("pages"), len(snapshots)
                  if "rows_received" not in coverage else coverage["rows_received"],
                  coverage.get("excluded", 0), coverage.get("unresolved", 0), len(grouped), len(events),
                  coverage["complete"], coverage.get("stop_reason"), coverage.get("elapsed_seconds"))
        for event in events:
            event["coverage_complete"] = coverage["complete"]
            if persist:
                await asyncio.to_thread(options_store.save_event, event)
        return events, coverage, len(snapshots)

    @tasks.loop(minutes=config.OPTIONS_SCAN_MINUTES)
    async def scan_loop(self):
        try:
            if config.MOOMOO_ENABLED and _within_market_hours():
                await self._run_scan()
        except Exception:
            _log.exception("Options scan failed; next run remains enabled")

    @scan_loop.before_loop
    async def before_scan_loop(self):
        await self.bot.wait_until_ready()

    async def _run_scan(self):
        async with self._scan_lock:
            start = clock.monotonic()
            coverage = {"profiles": {}}
            try:
                tickers = None if config.OPTIONS_UNIVERSE == "market" else config.OPTIONS_WATCHLIST
                _, coverage, _ = await self._scan(tickers)
                self._consecutive_failures, self._failure_alerted = 0, False
                options_pipeline_total.labels(stage="scan", status="success").inc()
            except Exception:
                options_pipeline_total.labels(stage="scan", status="error").inc()
                _log.exception("Options collection/scoring failed")
                try:
                    await self._record_scan_failure()
                except Exception:
                    _log.exception("Options failure notification could not be delivered")
            finally:
                options_scan_duration_seconds.observe(clock.monotonic() - start)
            # Delivery retries do not depend on the next market-data fetch succeeding.
            pending = await asyncio.to_thread(options_store.pending_events, datetime.now(timezone.utc), 3)
            sent = []
            for event in pending[:config.OPTIONS_MAX_ALERTS_PER_SCAN]:
                try:
                    delivery = await self._post_event(event, enrich=False)
                    if delivery:
                        sent.append((event, *delivery))
                except Exception:
                    _log.exception("Options Discord delivery failed; event remains pending")
            if sent and config.CATALYST_ENABLED:
                profiles = coverage["profiles"]
                if config.MOOMOO_TRANSPORT == "rest":
                    try:
                        profiles = await asyncio.wait_for(self._rest.profiles([e["ticker"] for e, _, _ in sent]), 10)
                    except Exception:
                        _log.warning("Optional company names unavailable; researching with ticker/local aliases")
                results = await asyncio.gather(*(self._enrich_event(e, message, embed, profiles.get(e["ticker"]))
                                                 for e, message, embed in sent), return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        _log.error("Catalyst enrichment failed after base delivery (%s)", type(result).__name__)

    async def _record_scan_failure(self):
        self._consecutive_failures += 1
        channel = _alert_channel(self.bot)
        if self._consecutive_failures >= 3 and not self._failure_alerted and channel:
            await channel.send(embed=discord.Embed(title="Options scanner unavailable",
                description=f"Three consecutive failures. Backend: {config.MOOMOO_TRANSPORT}. "
                            "Check credentials, market-data entitlements and logs.", color=discord.Color.orange()))
            self._failure_alerted = True

    async def _post_event(self, event, profile=None, *, enrich=True):
        if options_scan.excluded_underlying(event["ticker"]):
            return
        channel = _alert_channel(self.bot)
        if channel is None:
            return
        embed = event_embed(event)
        message = await channel.send(embed=embed)
        await asyncio.to_thread(options_store.complete_event, event["event_id"])
        options_pipeline_total.labels(stage="alert", status="sent").inc()
        if enrich and config.CATALYST_ENABLED:
            await self._enrich_event(event, message, embed, profile)
        return message, embed

    async def _enrich_event(self, event, message, embed, profile=None):
        async with self._research_slots:
            await self._research_and_edit(event, message, embed, profile)

    async def _research_and_edit(self, event, message, embed, profile=None):
        start = clock.monotonic()
        try:
            result = await asyncio.wait_for(catalyst_intelligence.investigate(event, profile),
                                            timeout=config.CATALYST_TIMEOUT_SECONDS)
        except Exception as exc:
            _log.warning("Catalyst enrichment unavailable (%s); base alert already sent", type(exc).__name__)
            result = {"status": "unavailable", "classification": "unavailable", "evidence_score": None,
                      "sources": [], "research": {"summary": "AI/search unavailable or budget exhausted; quantitative alert retained."}}
        result["elapsed_seconds"] = round(clock.monotonic() - start, 3)
        _log.info("Catalyst ticker=%s status=%s cache_hit=%s elapsed_seconds=%.3f",
                  event["ticker"], result.get("status"), result.get("cache_hit", False), result["elapsed_seconds"])
        if event.get("event_id"):
            await asyncio.to_thread(options_store.save_event_research, event["event_id"], result)
        try:
            await message.edit(embed=add_catalyst(embed, result))
        except Exception:
            _log.exception("Catalyst message update failed; base alert remains available")

    @tasks.loop(time=_DIGEST_TIMES)
    async def flush_digest(self):
        try:
            if config.MOOMOO_ENABLED and market_open_today():
                async with self._scan_lock:
                    await self._post_digest()
        except Exception:
            _log.exception("Options digest failed; pending events retained")

    @flush_digest.before_loop
    async def before_flush_digest(self):
        await self.bot.wait_until_ready()

    async def _post_digest(self):
        pending = await asyncio.to_thread(options_store.pending_events, market_now().astimezone(timezone.utc))
        if not pending:
            return
        channel = _alert_channel(self.bot)
        if channel is None:
            return
        for start in range(0, len(pending), 12):
            batch = pending[start:start + 12]
            lines = [f"**{e['ticker']}** {e['score']}/100 | {len(e['contracts'])} contracts | "
                     f"daily activity ${e['premium']:,.0f} | direction {e['directional_bias']}" for e in batch]
            embed = discord.Embed(title="Unusual Options Digest", description="\n".join(lines), color=discord.Color.blue())
            embed.set_footer(text="Selected aggregate activity, not confirmed buying or individual large trades.")
            await channel.send(embed=embed)
            for event in batch:
                await asyncio.to_thread(options_store.complete_event, event["event_id"])

    @commands.command(name="optionhealth", hidden=True)
    @commands.is_owner()
    async def cmd_option_health(self, ctx):
        if not config.MOOMOO_ENABLED:
            await ctx.send("Options scanning disabled (settings/options.json: moomoo.enabled).")
            return
        try:
            if config.MOOMOO_TRANSPORT == "rest":
                detail = await self._rest.health()
            else:
                _, detail = await asyncio.to_thread(moomoo_client.check_health)
            await ctx.send(detail)
        except Exception as exc:
            await ctx.send(f"Options health failed: {type(exc).__name__}. Check Railway logs.")
            _log.warning("Options health failed: %s", exc)

    @commands.command(name="optionscan", hidden=True)
    @commands.is_owner()
    async def cmd_option_scan(self, ctx):
        if not config.MOOMOO_ENABLED:
            await ctx.send("Options scanning disabled (settings/options.json: moomoo.enabled).")
            return
        if not _within_market_hours():
            await ctx.send("Market session closed. Use !optionflow TICKER for a non-persisted snapshot preview.")
            return
        await ctx.send("Scanning options activity...")
        await self._run_scan()
        await ctx.send("Scan attempted; check the options channel and logs. Empty scans are valid.")

    @commands.command(name="optionflow", hidden=True)
    @commands.is_owner()
    async def cmd_option_flow(self, ctx, ticker=None):
        await self._preview(ctx, ticker)

    @commands.command(name="optionresearch", hidden=True)
    @commands.is_owner()
    async def cmd_option_research(self, ctx, ticker=None):
        if not config.CATALYST_ENABLED:
            await ctx.send("Catalyst disabled (settings/options.json: catalyst.enabled).")
            return
        if not _within_market_hours():
            await ctx.send("Live research previews require an open market session to avoid stale option activity.")
            return
        await self._preview(ctx, ticker, research=True)

    async def _preview(self, ctx, ticker, *, research=False):
        if not config.MOOMOO_ENABLED:
            await ctx.send("Options scanning disabled (settings/options.json: moomoo.enabled).")
            return
        ticker = (ticker or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", ticker):
            await ctx.send("Usage: !optionresearch AAPL" if research else "Usage: !optionflow AAPL")
            return
        if options_scan.excluded_underlying(ticker):
            await ctx.send(f"{ticker}: index/broad-market ETF options are excluded from this workflow.")
            return
        async with self._scan_lock:
            async with ctx.typing():
                events, coverage, count = await self._scan([ticker], persist=False)
        if not events:
            await ctx.send(f"{ticker}: {count} candidates scanned, none above L2. Complete coverage: {coverage['complete']}.")
            return
        embed = event_embed(events[0])
        if research:
            embed.add_field(name="Manual Research Preview", value="Owner-requested L2+ candidate; not an automatic L3 alert.", inline=False)
        if not _within_market_hours():
            embed.add_field(name="Snapshot Preview", value="Market closed; this may be the previous session's activity.", inline=False)
        message = await ctx.send(embed=embed)
        if research:
            await self._enrich_event(events[0], message, embed, coverage["profiles"].get(ticker))


async def setup(bot):
    await bot.add_cog(OptionsFlow(bot))
