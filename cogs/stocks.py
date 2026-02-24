"""
stocks.py — Stock and index commands.
Commands: !stock, !compare, !market, !stockhelp
"""
import discord
from discord.ext import commands

from services.market_data import get_ticker_info
from utils.formatters import format_large_number, change_emoji, price_color, make_embed
from utils.constants import INDICES


class Stocks(commands.Cog):
    """Stock and index commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_stock_embed(self, ticker: str):
        """Return (embed, None) on success or (None, error_str) on failure."""
        ticker = ticker.upper()
        info = get_ticker_info(ticker)

        name = info.get("longName") or info.get("shortName")
        if not name:
            return None, (
                f"Could not find data for **{ticker}**. "
                "Please check the symbol and try again."
            )

        price      = info.get("currentPrice")    or info.get("regularMarketPrice")
        prev_close = info.get("previousClose")   or info.get("regularMarketPreviousClose")
        day_high   = info.get("dayHigh")         or info.get("regularMarketDayHigh")
        day_low    = info.get("dayLow")          or info.get("regularMarketDayLow")
        volume     = info.get("volume")          or info.get("regularMarketVolume")
        avg_volume = info.get("averageVolume")
        wk52_high  = info.get("fiftyTwoWeekHigh")
        wk52_low   = info.get("fiftyTwoWeekLow")
        market_cap = info.get("marketCap")
        pe_ratio   = info.get("trailingPE")
        dividend   = info.get("dividendYield")
        sector     = info.get("sector",   "N/A")
        industry   = info.get("industry", "N/A")
        exchange   = info.get("exchange", "N/A")
        currency   = info.get("currency", "USD")

        if price and prev_close:
            change     = price - prev_close
            change_pct = (change / prev_close) * 100
            sign       = "+" if change >= 0 else ""
            change_str = f"{sign}{change:.2f} ({sign}{change_pct:.2f}%)"
            icon       = change_emoji(change_pct)
        else:
            change     = None
            change_pct = 0
            change_str = "N/A"
            icon       = "\u27a1\ufe0f"

        if price and prev_close and change is not None:
            direction = "up" if change >= 0 else "down"
            summary = (
                f"{name} is trading at **{currency} {price:.2f}**, "
                f"{direction} **{abs(change_pct):.2f}%** from yesterday's close of {prev_close:.2f}."
            )
        else:
            summary = f"Price data for **{name}** is currently unavailable."

        if pe_ratio:   summary += f" P/E: {pe_ratio:.1f}."
        if dividend:   summary += f" Div yield: {dividend * 100:.2f}%."
        if market_cap: summary += f" Market cap: {format_large_number(market_cap)}."

        embed = make_embed(f"{icon} {name} ({ticker})", summary, price_color(change_pct))

        embed.add_field(name="Price",      value=f"{currency} {price:.2f}" if price else "N/A", inline=True)
        embed.add_field(name="Change",     value=change_str,                                     inline=True)
        embed.add_field(name="Exchange",   value=exchange,                                       inline=True)

        embed.add_field(name="Day High",   value=f"{day_high:.2f}"  if day_high  else "N/A",    inline=True)
        embed.add_field(name="Day Low",    value=f"{day_low:.2f}"   if day_low   else "N/A",    inline=True)
        embed.add_field(name="Market Cap", value=format_large_number(market_cap),                inline=True)

        embed.add_field(name="52W High",   value=f"{wk52_high:.2f}" if wk52_high else "N/A",    inline=True)
        embed.add_field(name="52W Low",    value=f"{wk52_low:.2f}"  if wk52_low  else "N/A",    inline=True)
        embed.add_field(name="P/E Ratio",  value=f"{pe_ratio:.2f}"  if pe_ratio  else "N/A",    inline=True)

        embed.add_field(name="Volume",     value=f"{volume:,}"      if volume     else "N/A",    inline=True)
        embed.add_field(name="Avg Volume", value=f"{avg_volume:,}"  if avg_volume else "N/A",    inline=True)
        embed.add_field(name="Div Yield",  value=f"{dividend*100:.2f}%" if dividend else "N/A",  inline=True)

        embed.add_field(name="Sector",   value=sector,   inline=True)
        embed.add_field(name="Industry", value=industry, inline=True)

        return embed, None

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="stock", help="Detailed stock summary. Usage: !stock AAPL")
    async def cmd_stock(self, ctx, ticker: str = None):
        if ticker is None:
            await ctx.send("Please provide a ticker.  Example: `!stock AAPL`")
            return
        async with ctx.typing():
            embed, error = self._build_stock_embed(ticker)
        if error:
            await ctx.send(error)
        else:
            await ctx.send(embed=embed)

    @commands.command(name="compare", help="Compare 2-3 stocks side by side. Usage: !compare AAPL MSFT GOOG")
    async def cmd_compare(self, ctx, *tickers):
        if not tickers:
            await ctx.send("Provide 2-3 tickers.  Example: `!compare AAPL MSFT GOOG`")
            return
        if len(tickers) > 3:
            await ctx.send("Please compare at most 3 tickers at a time.")
            return
        async with ctx.typing():
            for t in tickers:
                embed, error = self._build_stock_embed(t)
                if error:
                    await ctx.send(error)
                else:
                    await ctx.send(embed=embed)

    @commands.command(name="market", help="Quick snapshot of major indices.")
    async def cmd_market(self, ctx):
        embed = make_embed("\U0001f4ca Market Snapshot", color=discord.Color.blurple())
        async with ctx.typing():
            lines = []
            for label, sym in INDICES.items():
                info       = get_ticker_info(sym)
                price      = info.get("regularMarketPrice") or info.get("currentPrice")
                prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
                if price and prev_close:
                    pct   = (price - prev_close) / prev_close * 100
                    arrow = "\u25b2" if pct >= 0 else "\u25bc"
                    sign  = "+" if pct >= 0 else ""
                    lines.append(f"**{label}** (`{sym}`)\n{arrow} {price:,.2f}  ({sign}{pct:.2f}%)")
                else:
                    lines.append(f"**{label}** \u2014 data unavailable")
        embed.description = "\n\n".join(lines)
        await ctx.send(embed=embed)

    @commands.command(name="stockhelp", help="List all bot commands.")
    async def cmd_stockhelp(self, ctx):
        embed = make_embed("Trading Bot \u2014 Available Commands", color=discord.Color.gold())
        embed.add_field(name="`!stock <TICKER>`",         value="Full summary for one stock.\nExample: `!stock TSLA`",          inline=False)
        embed.add_field(name="`!compare <T1> <T2> [T3]`", value="Compare 2-3 stocks.\nExample: `!compare AAPL MSFT GOOG`",      inline=False)
        embed.add_field(name="`!market`",                 value="Live snapshot of S&P 500, Dow, NASDAQ, Russell 2000, VIX.",     inline=False)
        embed.add_field(name="`!crypto`",                 value="Snapshot of top cryptocurrencies.",                            inline=False)
        embed.add_field(name="`!btc`",                    value="Quick Bitcoin price check.",                                   inline=False)
        embed.add_field(name="`!commodities`",            value="Gold, silver, oil, and natural gas prices.",                   inline=False)
        embed.add_field(name="`!options <TICKER>`",       value="Top options by open interest.\nExample: `!options SPY`",       inline=False)
        embed.add_field(name="`!earnings <TICKER>`",      value="Upcoming earnings calendar.\nExample: `!earnings AAPL`",       inline=False)
        embed.add_field(name="`!macro`",                  value="Key macro indicators (yields, dollar index).",                 inline=False)
        embed.add_field(name="`!analyze <TICKER>`",       value="AI-powered technical analysis.\nExample: `!analyze AAPL`",     inline=False)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stocks(bot))
