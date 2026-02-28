"""
analysis.py — LLM-powered technical analysis commands.
Commands: !analyze <TICKER>
Requires ANTHROPIC_API_KEY in .env.
"""
from discord.ext import commands

from services.market_data import get_ticker_info
from services.llm_client import analyze_ticker
from utils.formatters import format_large_number, make_embed


class Analysis(commands.Cog):
    """LLM-powered technical analysis commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="analyze", help="AI technical analysis for a ticker. Usage: !analyze AAPL")
    async def cmd_analyze(self, ctx, ticker: str = None):
        if ticker is None:
            await ctx.send("Please provide a ticker.  Example: `!analyze AAPL`")
            return
        ticker = ticker.upper()
        async with ctx.typing():
            info = get_ticker_info(ticker)
            name       = info.get("longName") or info.get("shortName") or ticker
            price      = info.get("currentPrice")    or info.get("regularMarketPrice")
            prev_close = info.get("previousClose")   or info.get("regularMarketPreviousClose")
            wk52_high  = info.get("fiftyTwoWeekHigh")
            wk52_low   = info.get("fiftyTwoWeekLow")
            market_cap = info.get("marketCap")
            pe_ratio   = info.get("trailingPE")

            price_summary = (
                f"Ticker: {ticker} ({name})\n"
                f"Current Price: {price}\n"
                f"Previous Close: {prev_close}\n"
                f"52-Week High: {wk52_high}\n"
                f"52-Week Low: {wk52_low}\n"
                f"Market Cap: {format_large_number(market_cap)}\n"
                f"P/E Ratio: {pe_ratio}\n"
            )
            analysis = await analyze_ticker(ticker, price_summary)

        embed = make_embed(f"\U0001f916 AI Analysis: {ticker}", analysis)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Analysis(bot))
