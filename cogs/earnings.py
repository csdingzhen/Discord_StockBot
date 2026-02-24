"""
earnings.py — Earnings calendar commands.
Commands: !earnings <TICKER>
"""
from discord.ext import commands

from services.market_data import get_calendar
from utils.formatters import make_embed


class Earnings(commands.Cog):
    """Earnings-related commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="earnings", help="Upcoming earnings data for a ticker. Usage: !earnings AAPL")
    async def cmd_earnings(self, ctx, ticker: str = None):
        if ticker is None:
            await ctx.send("Please provide a ticker.  Example: `!earnings AAPL`")
            return
        ticker = ticker.upper()
        async with ctx.typing():
            cal = get_calendar(ticker)
            if cal is None or (hasattr(cal, "empty") and cal.empty):
                await ctx.send(f"No upcoming earnings data found for **{ticker}**.")
                return
            embed = make_embed(f"\U0001f4c5 Earnings: {ticker}")
            # cal may be a dict or DataFrame depending on yfinance version
            if hasattr(cal, "columns"):
                for col in cal.columns:
                    val = cal[col].iloc[0]
                    embed.add_field(name=str(col), value=str(val) if val is not None else "N/A", inline=True)
            elif isinstance(cal, dict):
                for key, val in cal.items():
                    embed.add_field(name=str(key), value=str(val) if val is not None else "N/A", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Earnings(bot))
