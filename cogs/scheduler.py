"""
scheduler.py — Scheduled market update pushes.

Fires after NYSE close:
  - 4:05 PM ET on normal trading days
  - 1:05 PM ET on early-close days (e.g. day before Thanksgiving, Christmas Eve)
  - Skipped entirely on NYSE holidays and weekends

Set MARKET_CHANNEL_ID in .env to enable.
"""
import discord
from discord.ext import commands, tasks
from datetime import time, date

import pytz
import pandas_market_calendars as mcal

import config
from services.market_data import get_ticker_info
from utils.formatters import change_emoji, make_embed
from utils.constants import INDICES

ET = pytz.timezone("America/New_York")
_NORMAL_CLOSE_HOUR = 16  # 4:00 PM ET


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
    return close_et.hour < _NORMAL_CLOSE_HOUR


# ------------------------------------------------------------------
# Cog
# ------------------------------------------------------------------

class Scheduler(commands.Cog):
    """Scheduled market update pushes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.normal_close_update.start()
        self.early_close_update.start()

    def cog_unload(self):
        self.normal_close_update.cancel()
        self.early_close_update.cancel()

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
        embed.description = "\n".join(lines)
        await channel.send(embed=embed)

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
