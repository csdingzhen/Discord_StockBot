"""
scheduler.py — Scheduled market update pushes.

Fires after NYSE close:
  - 4:05 PM ET on normal trading days
  - 1:05 PM ET on early-close days (e.g. day before Thanksgiving, Christmas Eve)
  - Skipped entirely on NYSE holidays and weekends

Set MARKET_CHANNEL_ID in .env to enable.
"""
import asyncio

import discord
import requests
from discord.ext import commands, tasks
from datetime import time, date

import pytz
import pandas_market_calendars as mcal

import config
from services.market_data import get_ticker_info
from services.premarket_data import fetch_premarket_snapshot, build_data_summary
from services.llm_client import analyze_premarket
from utils.formatters import change_emoji, make_embed
from utils.constants import (
    INDICES,
    PREMARKET_EMOJIS,
    PREMARKET_LABELS_BILINGUAL,
    VIX_PANIC_THRESHOLD,
    VIX_ELEVATED_THRESHOLD,
    NORMAL_CLOSE_HOUR,
    CNN_FEAR_GREED_URL,
    CNN_FEAR_GREED_HEADERS,
)

ET = pytz.timezone("America/New_York")


# ------------------------------------------------------------------
# Calendar helpers
# ------------------------------------------------------------------

def _nyse_schedule_today():
    """Return today's NYSE schedule row, or None if the market is closed."""
    nyse = mcal.get_calendar("NYSE")
    today = date.today().strftime("%Y-%m-%d")
    schedule = nyse.schedule(start_date=today, end_date=today)
    return None if schedule.empty else schedule.iloc[0]


def market_open_today() -> bool:
    """True if NYSE has a session today (not a holiday or weekend)."""
    return _nyse_schedule_today() is not None


def is_early_close_today() -> bool:
    """True if NYSE closes before 4:00 PM ET today (e.g. 1:00 PM ET)."""
    row = _nyse_schedule_today()
    if row is None:
        return False
    close_et = row["market_close"].astimezone(ET)
    return close_et.hour < NORMAL_CLOSE_HOUR


# ------------------------------------------------------------------
# CNN Fear and Greed
# ------------------------------------------------------------------

def fetch_fear_and_greed():
    """Return (score, rating) from the CNN Fear & Greed index, or a fallback string."""
    try:
        response = requests.get(CNN_FEAR_GREED_URL, headers=CNN_FEAR_GREED_HEADERS)
        data = response.json()
    except Exception:
        data = None
    if not data:
        return "Information Not Available"
    score  = data["fear_and_greed"]["score"]
    rating = data["fear_and_greed"]["rating"]
    return score, rating


# ------------------------------------------------------------------
# Cog
# ------------------------------------------------------------------

class Scheduler(commands.Cog):
    """Scheduled market update pushes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.normal_close_update.start()
        self.early_close_update.start()
        self.premarket_update.start()

    def cog_unload(self):
        self.normal_close_update.cancel()
        self.early_close_update.cancel()
        self.premarket_update.cancel()

    # ------------------------------------------------------------------
    # Shared embed sender
    # ------------------------------------------------------------------

    async def _send_summary(self):
        channel_id = config.MARKET_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        embed = make_embed("\U0001f4ca Daily Market Summary", color=discord.Color.blurple())
        lines = []
        for label, sym in INDICES.items():
            info       = get_ticker_info(sym)
            price      = info.get("regularMarketPrice") or info.get("currentPrice")
            prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
            if price and prev_close:
                pct  = (price - prev_close) / prev_close * 100
                icon = change_emoji(pct)
                sign = "+" if pct >= 0 else ""
                lines.append(f"**{label}**  {icon} {price:,.2f}  ({sign}{pct:.2f}%)")
            else:
                lines.append(f"**{label}** \u2014 unavailable")
        fear_and_greed = fetch_fear_and_greed()
        lines.append(f"**CNN Fear & Greed Index** {fear_and_greed[0]:,.2f} {fear_and_greed[1]}")
        embed.description = "\n".join(lines)
        await channel.send(embed=embed)

    # ------------------------------------------------------------------
    # Pre-market brief sender
    # ------------------------------------------------------------------

    async def _send_premarket_brief(self):
        channel_id = config.MARKET_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        snapshot = await asyncio.to_thread(fetch_premarket_snapshot)

        lines = []
        for label, data in snapshot.items():
            display = PREMARKET_LABELS_BILINGUAL.get(label, label)

            if label == "Gold/Oil Ratio":
                val = data.get("value")
                ratio_str = f"{val:.2f}" if val else "N/A"
                lines.append(f"**⚖️ {display}**  {ratio_str}")
                continue

            icon  = PREMARKET_EMOJIS.get(label, "•")
            price = data.get("price")
            pct   = data.get("pct_change")

            if price is None:
                lines.append(f"**{icon} {display}**  — unavailable")
                continue

            dir_icon = change_emoji(pct) if pct is not None else "➡️"
            sign     = "+" if (pct or 0) >= 0 else ""
            pct_str  = f"({sign}{pct:.2f}%)" if pct is not None else ""

            vix_note = ""
            if label == "VIX":
                if price >= VIX_PANIC_THRESHOLD:
                    vix_note = "  🔴 PANIC"
                elif price >= VIX_ELEVATED_THRESHOLD:
                    vix_note = "  ⚠️ Elevated"
                else:
                    vix_note = "  ✅ Normal"

            lines.append(
                f"**{icon} {display}**  {dir_icon} {price:,.2f}  {pct_str}{vix_note}"
            )

        data_summary = build_data_summary(snapshot)
        analysis     = await analyze_premarket(data_summary)

        lines.append(f"\n**🤖 AI分析**\n{analysis}")

        embed = make_embed("📊 Pre-Market Briefing 盘前简报", color=discord.Color.orange())
        embed.description = "\n".join(lines)
        await channel.send(embed=embed)

    # ------------------------------------------------------------------
    # Dev: manual triggers for testing
    # ------------------------------------------------------------------

    @commands.command(name="premarket", hidden=True)
    @commands.is_owner()
    async def force_premarket_brief(self, ctx):
        """Owner-only: manually fire the pre-market brief right now."""
        await ctx.send("Fetching pre-market data…", delete_after=5)
        await self._send_premarket_brief()
        await ctx.send("Pre-market brief sent.", delete_after=5)

    @commands.command(name="marketsummary", hidden=True)
    @commands.is_owner()
    async def force_market_summary(self, ctx):
        """Owner-only: manually fire the market summary right now."""
        await self._send_summary()
        await ctx.send("Market summary sent.", delete_after=5)

    # ------------------------------------------------------------------
    # Pre-market: 9:00 AM ET — fires on all trading days
    # ------------------------------------------------------------------

    @tasks.loop(time=time(9, 0, tzinfo=ET))
    async def premarket_update(self):
        if not market_open_today():
            return
        await self._send_premarket_brief()

    @premarket_update.before_loop
    async def before_premarket(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Normal close: 4:05 PM ET
    # Skips on NYSE holidays, weekends, and early-close days
    # ------------------------------------------------------------------

    @tasks.loop(time=time(16, 5, tzinfo=ET))
    async def normal_close_update(self):
        if not market_open_today() or is_early_close_today():
            return
        await self._send_summary()

    @normal_close_update.before_loop
    async def before_normal_close(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Early close: 1:05 PM ET
    # Only fires on early-close days (e.g. day before Thanksgiving, Christmas Eve)
    # ------------------------------------------------------------------

    @tasks.loop(time=time(13, 5, tzinfo=ET))
    async def early_close_update(self):
        if not market_open_today() or not is_early_close_today():
            return
        await self._send_summary()

    @early_close_update.before_loop
    async def before_early_close(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
