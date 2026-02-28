"""
commodities.py — Commodity price commands (gold, silver, oil, natural gas).
Commands: !commodities
"""
from discord.ext import commands

from services.market_data import get_ticker_info
from utils.formatters import change_emoji, make_embed
from utils.constants import COMMODITY_TICKERS


class Commodities(commands.Cog):
    """Commodity price commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="commodities", help="Gold, silver, oil, and natural gas prices.")
    async def cmd_commodities(self, ctx):
        embed = make_embed("\U0001f4b0 Commodities Snapshot")
        async with ctx.typing():
            lines = []
            for name, sym in COMMODITY_TICKERS.items():
                info       = get_ticker_info(sym)
                price      = info.get("regularMarketPrice") or info.get("currentPrice")
                prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
                if price and prev_close:
                    pct  = (price - prev_close) / prev_close * 100
                    icon = change_emoji(pct)
                    sign = "+" if pct >= 0 else ""
                    lines.append(f"**{name}** (`{sym}`)\n{icon} ${price:,.2f}  ({sign}{pct:.2f}%)")
                else:
                    lines.append(f"**{name}** \u2014 data unavailable")
        embed.description = "\n\n".join(lines)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Commodities(bot))
