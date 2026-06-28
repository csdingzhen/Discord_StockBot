"""
options_flow.py — unusual options-activity pipeline (moomoo).

Mirrors the news pipeline's tiering:
  L1 (below interest) -> snapshot stored only, never posted.
  L2 (1 strong signal) -> recorded, rolled into a scheduled digest.
  L3 (>=2 strong signals or extreme notional) -> immediate AI-analysis alert.

Gated behind config.MOOMOO_ENABLED: when off (no OpenD on this machine), the
loops no-op so the rest of the bot runs untouched. The heavy synchronous
moomoo SDK calls run via asyncio.to_thread; scoring is pure (services.options_scan).
"""
import asyncio
import json
from datetime import date, time

import discord
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo

import config
from cogs.scheduler import market_open_today
from services import llm_client, moomoo_client, options_scan
from storage import options_store
from utils.constants import OPTIONS_WATCHLIST

ET = ZoneInfo("America/New_York")

# Intraday roll-ups of L2 flow, plus an end-of-session wrap.
_DIGEST_TIMES = [
    time(11, 30, tzinfo=ET),
    time(13, 30, tzinfo=ET),
    time(15, 30, tzinfo=ET),
    time(16, 15, tzinfo=ET),
]

# After this many consecutive scan failures (~1h at 20-min cadence), warn once.
_FAILURE_ALERT_THRESHOLD = 3

# Per-scan cap on immediate alerts so a market-wide IV spike can't flood the
# channel; the rest still land in the digest.
_MAX_L3_PER_SCAN = 8


def _alert_channel(bot):
    cid = config.OPTIONS_ALERT_CHANNEL_ID
    return bot.get_channel(cid) if cid else None


class OptionsFlow(commands.Cog):
    """Polling, scoring, and delivery of unusual options activity."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        options_store.init_db()
        self._consecutive_failures = 0
        self._failure_alerted = False
        # In-memory contract universe per ticker, rebuilt once per day.
        self._universe: dict[str, list[str]] = {}
        self._universe_date: str | None = None
        self.scan_loop.start()
        self.flush_digest.start()

    def cog_unload(self):
        self.scan_loop.cancel()
        self.flush_digest.cancel()

    # ------------------------------------------------------------------
    # Universe (contract codes) — built once per day per ticker
    # ------------------------------------------------------------------

    async def _get_universe(self, ticker: str) -> list[str]:
        today = date.today().isoformat()
        if self._universe_date != today:
            self._universe = {}
            self._universe_date = today
        if ticker not in self._universe:
            try:
                self._universe[ticker] = await asyncio.to_thread(
                    moomoo_client.build_universe, ticker
                )
            except Exception as e:
                print(f"[options] build_universe failed for {ticker}: {e}")
                self._universe[ticker] = []
        return self._universe[ticker]

    # ------------------------------------------------------------------
    # Scan loop
    # ------------------------------------------------------------------

    @tasks.loop(minutes=20)
    async def scan_loop(self):
        if not config.MOOMOO_ENABLED or not market_open_today():
            return
        await self._run_scan()

    async def _run_scan(self):
        today = date.today().isoformat()
        l3_posted = 0
        successes = 0

        for ticker in sorted(OPTIONS_WATCHLIST):
            try:
                codes = await self._get_universe(ticker)
                if not codes:
                    continue
                underlying_price, snaps = await asyncio.to_thread(
                    moomoo_client.fetch_ticker_snapshots, ticker, codes
                )
                successes += 1
                if not snaps:
                    continue
                await asyncio.to_thread(
                    options_store.save_snapshots, snaps, today, underlying_price
                )
                prior_iv = await asyncio.to_thread(
                    options_store.get_prior_iv_map, ticker, today
                )
                anomalies = options_scan.detect_anomalies(snaps, prior_iv)

                for a in anomalies:
                    signals_json = json.dumps(
                        {"reasons": a["reasons"], "notional": a["notional"],
                         "vol_oi_ratio": a["vol_oi_ratio"], "iv": a["iv"],
                         "strike": a["strike"], "expiry": a["expiry"],
                         "option_type": a["option_type"]},
                        ensure_ascii=False,
                    )
                    newly = await asyncio.to_thread(
                        options_store.record_alert, a, today, signals_json
                    )
                    if a["tier"] == 3 and newly and l3_posted < _MAX_L3_PER_SCAN:
                        await self._post_anomaly_alert(a, underlying_price)
                        l3_posted += 1
            except Exception as e:
                print(f"[options] scan failed for {ticker}: {e}")
            await asyncio.sleep(1)  # courtesy spacing under moomoo rate limits

        # Only a total wipeout (every ticker failed -> almost always OpenD
        # down) counts as an outage; isolated per-ticker errors are normal.
        if successes == 0:
            await self._record_scan_failure()
        else:
            self._record_scan_success()

    @scan_loop.before_loop
    async def before_scan_loop(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Failure / outage tracking
    # ------------------------------------------------------------------

    async def _record_scan_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAILURE_ALERT_THRESHOLD and not self._failure_alerted:
            self._failure_alerted = True
            channel = _alert_channel(self.bot)
            if channel:
                await channel.send(embed=discord.Embed(
                    title="⚠️ 期权异动扫描连续失败",
                    description=(
                        f"已连续 {self._consecutive_failures} 轮扫描出现错误，"
                        "请检查 moomoo OpenD 是否在线并已登录。"
                    ),
                    color=discord.Color.orange(),
                ))

    def _record_scan_success(self):
        self._consecutive_failures = 0
        self._failure_alerted = False

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    async def _post_anomaly_alert(self, anomaly: dict, underlying_price: float | None):
        channel = _alert_channel(self.bot)
        if channel is None:
            return

        label = options_scan.contract_label(anomaly)
        reasons = options_scan.reason_labels(anomaly["reasons"])
        try:
            ai_block = await llm_client.analyze_options_anomaly(
                contract_label=label,
                option_type=anomaly["option_type"],
                signals=reasons,
                metrics=anomaly,
                underlying_price=underlying_price,
            )
        except Exception as e:
            print(f"[options] anomaly analysis failed: {e}")
            ai_block = None

        color = discord.Color.green() if anomaly["option_type"] == "CALL" else discord.Color.red()
        embed = discord.Embed(title=f"🚨 期权异动 | {label}", color=color)
        embed.add_field(name="触发信号", value="、".join(reasons) or "—", inline=False)

        stats = [
            f"成交量 {anomaly['volume']:,}" + (f" / 持仓 {anomaly['open_interest']:,}" if anomaly.get("open_interest") else ""),
        ]
        if anomaly.get("vol_oi_ratio") is not None:
            stats.append(f"量/仓比 {anomaly['vol_oi_ratio']:.1f}")
        if anomaly.get("notional") is not None:
            stats.append(f"名义成交额(估) ${anomaly['notional']:,.0f}")
        if anomaly.get("iv") is not None:
            iv_line = f"IV {anomaly['iv']:.1f}%"
            if anomaly.get("iv_jump") is not None:
                iv_line += f" ({anomaly['iv_jump']:+.1f})"
            stats.append(iv_line)
        if anomaly.get("dte") is not None:
            stats.append(f"剩余 {anomaly['dte']}天")
        if underlying_price is not None:
            stats.append(f"正股 ${underlying_price:,.2f}")
        embed.add_field(name="数据", value="\n".join(stats), inline=False)

        if ai_block:
            embed.add_field(name="🤖 解读", value=ai_block, inline=False)
        embed.set_footer(text="名义成交额为 量×价×乘数 估算，非真实大单逐笔数据")
        await channel.send(embed=embed)

    # ------------------------------------------------------------------
    # Digest: roll up pending L2 items at scheduled times
    # ------------------------------------------------------------------

    @tasks.loop(time=_DIGEST_TIMES)
    async def flush_digest(self):
        if not config.MOOMOO_ENABLED or not market_open_today():
            return
        await self._post_digest()

    @flush_digest.before_loop
    async def before_flush_digest(self):
        await self.bot.wait_until_ready()

    async def _post_digest(self):
        today = date.today().isoformat()
        pending = await asyncio.to_thread(options_store.get_pending_digest, today)
        if not pending:
            return
        channel = _alert_channel(self.bot)
        if channel is None:
            return

        lines = []
        for row in pending:
            try:
                sig = json.loads(row["signals_json"])
            except Exception:
                sig = {}
            cp = "C" if sig.get("option_type") == "CALL" else "P"
            strike = sig.get("strike")
            strike_str = f"{strike:g}" if strike is not None else "?"
            reasons = "、".join(options_scan.reason_labels(sig.get("reasons", [])))
            notional = sig.get("notional")
            notional_str = f"${notional:,.0f}" if notional is not None else ""
            lines.append(f"`{row['ticker']} {strike_str}{cp} {sig.get('expiry','')}` — {reasons} {notional_str}".strip())

        embed = discord.Embed(
            title=f"📋 期权异动摘要（{len(pending)}条）",
            description="\n".join(lines)[:4096],
            color=discord.Color.blue(),
        )
        await channel.send(embed=embed)
        await asyncio.to_thread(
            options_store.mark_digested, [r["contract_code"] for r in pending], today
        )

    # ------------------------------------------------------------------
    # Owner commands
    # ------------------------------------------------------------------

    @commands.command(name="optionhealth", hidden=True)
    @commands.is_owner()
    async def cmd_option_health(self, ctx):
        """Owner-only: check moomoo OpenD connectivity."""
        ok, detail = await asyncio.to_thread(moomoo_client.check_health)
        await ctx.send(f"{'✅' if ok else '❌'} moomoo: {detail}")

    @commands.command(name="optionscan", hidden=True)
    @commands.is_owner()
    async def cmd_option_scan(self, ctx):
        """Owner-only: fire one full options-flow scan right now."""
        if not config.MOOMOO_ENABLED:
            await ctx.send("MOOMOO_ENABLED is false — set it in .env and restart.")
            return
        await ctx.send("Scanning options flow…", delete_after=5)
        await self._run_scan()
        await ctx.send("Options scan complete.", delete_after=5)

    @commands.command(name="optionflow", hidden=True)
    @commands.is_owner()
    async def cmd_option_flow(self, ctx, ticker: str = None):
        """Owner-only: scan one ticker on demand. !optionflow AAPL"""
        if not config.MOOMOO_ENABLED:
            await ctx.send("MOOMOO_ENABLED is false — set it in .env and restart.")
            return
        if ticker is None:
            await ctx.send("Usage: `!optionflow AAPL`")
            return
        ticker = ticker.upper()
        async with ctx.typing():
            codes = await self._get_universe(ticker)
            if not codes:
                await ctx.send(f"No option universe built for **{ticker}** (check OpenD / permissions).")
                return
            underlying_price, snaps = await asyncio.to_thread(
                moomoo_client.fetch_ticker_snapshots, ticker, codes
            )
            today = date.today().isoformat()
            prior_iv = await asyncio.to_thread(options_store.get_prior_iv_map, ticker, today)
            anomalies = options_scan.detect_anomalies(snaps, prior_iv)

        if not anomalies:
            await ctx.send(f"**{ticker}**: {len(snaps)} contracts scanned, no anomalies above L2.")
            return
        lines = [
            f"`L{a['tier']}` {options_scan.contract_label(a)} — "
            f"{'、'.join(options_scan.reason_labels(a['reasons']))} "
            f"(${a['notional']:,.0f})"
            for a in anomalies[:15]
        ]
        await ctx.send(embed=discord.Embed(
            title=f"期权异动: {ticker}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(OptionsFlow(bot))
