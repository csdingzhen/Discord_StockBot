"""
macro.py — Macro-economic indicator commands.
Commands: !macro
"""
from discord.ext import commands

from services.market_data import get_ticker_info
from utils.formatters import make_embed
from utils.constants import MACRO_TICKERS


class Macro(commands.Cog):
    """Macro-economic indicator commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="macro", help="Key macro indicators (yields, dollar index).")
    async def cmd_macro(self, ctx):
        embed = make_embed("\U0001f30e Macro Indicators")
        async with ctx.typing():
            for label, sym in MACRO_TICKERS.items():
                info  = get_ticker_info(sym)
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                embed.add_field(
                    name=label,
                    value=f"{price:.4f}" if price else "N/A",
                    inline=True,
                )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Macro(bot))
