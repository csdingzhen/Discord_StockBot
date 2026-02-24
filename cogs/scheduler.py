"""
scheduler.py — Scheduled market update pushes.
Sends a daily market summary to MARKET_CHANNEL_ID at bot startup + every 24 h.
Set MARKET_CHANNEL_ID in .env to enable.
"""
import discord
from discord.ext import commands, tasks

import config
from services.market_data import get_ticker_info
from utils.formatters import change_emoji, make_embed
from utils.constants import INDICES


class Scheduler(commands.Cog):
    """Scheduled market update pushes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_market_update.start()

    def cog_unload(self):
        self.daily_market_update.cancel()

    # ------------------------------------------------------------------
    # Scheduled task — fires every 24 hours
    # ------------------------------------------------------------------

    @tasks.loop(hours=24)
    async def daily_market_update(self):
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

    @daily_market_update.before_loop
    async def before_daily_update(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Scheduler(bot))
