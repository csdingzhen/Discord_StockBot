"""
options.py — Options chain commands.
Commands: !options <TICKER>
"""
from discord.ext import commands

from services.options_data import get_nearest_expiry_chain, format_options_summary
from utils.formatters import make_embed


class Options(commands.Cog):
    """Options chain commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="options", help="Top options by open interest for a ticker. Usage: !options SPY")
    async def cmd_options(self, ctx, ticker: str = None):
        if ticker is None:
            await ctx.send("Please provide a ticker.  Example: `!options SPY`")
            return
        ticker = ticker.upper()
        async with ctx.typing():
            calls, puts, dates = get_nearest_expiry_chain(ticker)
            if calls is None:
                await ctx.send(f"No options data found for **{ticker}**.")
                return
            summary      = format_options_summary(calls, puts)
            expiry_label = dates[0] if dates else "N/A"
        embed = make_embed(f"\U0001f4ca Options: {ticker}  (exp {expiry_label})", summary)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Options(bot))
