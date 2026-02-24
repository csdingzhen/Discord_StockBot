import os
import discord
from discord.ext import commands
import yfinance as yf
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")  # export DISCORD_TOKEN="your-token-here"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_large_number(n):
    """Format a number into a human-readable dollar string."""
    if n is None:
        return "N/A"
    if n >= 1_000_000_000_000:
        return f"${n / 1_000_000_000_000:.2f}T"
    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.2f}M"
    return f"${n:,.2f}"


def change_emoji(pct):
    if pct > 2:
        return "\U0001f680"   # rocket
    if pct > 0:
        return "\U0001f4c8"   # chart up
    if pct < -2:
        return "\U0001f534"   # red circle
    if pct < 0:
        return "\U0001f4c9"   # chart down
    return "\u27a1\ufe0f"     # right arrow


def build_stock_embed(ticker: str):
    """
    Fetch yfinance data for *ticker* and return a (discord.Embed, None) tuple,
    or (None, error_string) on failure.
    """
    ticker = ticker.upper()
    info = yf.Ticker(ticker).info

    name = info.get("longName") or info.get("shortName")
    if not name:
        return None, (
            f"Could not find data for **{ticker}**. "
            "Please check the symbol and try again."
        )

    price        = info.get("currentPrice") or info.get("regularMarketPrice")
    prev_close   = info.get("previousClose") or info.get("regularMarketPreviousClose")
    day_high     = info.get("dayHigh") or info.get("regularMarketDayHigh")
    day_low      = info.get("dayLow")  or info.get("regularMarketDayLow")
    volume       = info.get("volume")  or info.get("regularMarketVolume")
    avg_volume   = info.get("averageVolume")
    wk52_high    = info.get("fiftyTwoWeekHigh")
    wk52_low     = info.get("fiftyTwoWeekLow")
    market_cap   = info.get("marketCap")
    pe_ratio     = info.get("trailingPE")
    dividend     = info.get("dividendYield")
    sector       = info.get("sector", "N/A")
    industry     = info.get("industry", "N/A")
    exchange     = info.get("exchange", "N/A")
    currency     = info.get("currency", "USD")

    # Change calculation
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

    # One-sentence summary
    if price and prev_close and change is not None:
        direction = "up" if change >= 0 else "down"
        summary = (
            f"{name} is trading at **{currency} {price:.2f}**, "
            f"{direction} **{abs(change_pct):.2f}%** from yesterday's close of {prev_close:.2f}."
        )
    else:
        summary = f"Price data for **{name}** is currently unavailable."

    if pe_ratio:
        summary += f" P/E: {pe_ratio:.1f}."
    if dividend:
        summary += f" Div yield: {dividend * 100:.2f}%."
    if market_cap:
        summary += f" Market cap: {format_large_number(market_cap)}."

    color = discord.Color.green() if change_pct >= 0 else discord.Color.red()
    embed = discord.Embed(
        title=f"{icon} {name} ({ticker})",
        description=summary,
        color=color,
        timestamp=datetime.utcnow(),
    )

    embed.add_field(name="Price",       value=f"{currency} {price:.2f}" if price else "N/A", inline=True)
    embed.add_field(name="Change",      value=change_str,                                      inline=True)
    embed.add_field(name="Exchange",    value=exchange,                                         inline=True)

    embed.add_field(name="Day High",    value=f"{day_high:.2f}"  if day_high  else "N/A",     inline=True)
    embed.add_field(name="Day Low",     value=f"{day_low:.2f}"   if day_low   else "N/A",     inline=True)
    embed.add_field(name="Market Cap",  value=format_large_number(market_cap),                  inline=True)

    embed.add_field(name="52W High",    value=f"{wk52_high:.2f}" if wk52_high else "N/A",     inline=True)
    embed.add_field(name="52W Low",     value=f"{wk52_low:.2f}"  if wk52_low  else "N/A",     inline=True)
    embed.add_field(name="P/E Ratio",   value=f"{pe_ratio:.2f}"  if pe_ratio  else "N/A",     inline=True)

    embed.add_field(name="Volume",      value=f"{volume:,}"      if volume      else "N/A",   inline=True)
    embed.add_field(name="Avg Volume",  value=f"{avg_volume:,}"  if avg_volume  else "N/A",   inline=True)
    embed.add_field(name="Div Yield",   value=f"{dividend*100:.2f}%" if dividend else "N/A",   inline=True)

    embed.add_field(name="Sector",      value=sector,   inline=True)
    embed.add_field(name="Industry",    value=industry, inline=True)

    embed.set_footer(text="Data via Yahoo Finance  |  prices may be delayed up to 15 min")
    return embed, None


# ---------------------------------------------------------------------------
# Bot events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}  (ID: {bot.user.id})")
    print("Commands:  !stock <TICKER>   !compare <T1> <T2> [T3]   !market   !stockhelp")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@bot.command(name="stock", help="Detailed stock summary.  Usage: !stock AAPL")
async def cmd_stock(ctx, ticker: str = None):
    """Fetch and post a summary embed for a single ticker."""
    if ticker is None:
        await ctx.send("Please provide a ticker.  Example: `!stock AAPL`")
        return
    async with ctx.typing():
        embed, error = build_stock_embed(ticker)
    if error:
        await ctx.send(error)
    else:
        await ctx.send(embed=embed)


@bot.command(name="compare", help="Compare 2-3 stocks side by side.  Usage: !compare AAPL MSFT GOOG")
async def cmd_compare(ctx, *tickers):
    """Post a summary embed for each ticker in the list (max 3)."""
    if not tickers:
        await ctx.send("Provide 2-3 tickers.  Example: `!compare AAPL MSFT GOOG`")
        return
    if len(tickers) > 3:
        await ctx.send("Please compare at most 3 tickers at a time.")
        return
    async with ctx.typing():
        for t in tickers:
            embed, error = build_stock_embed(t)
            if error:
                await ctx.send(error)
            else:
                await ctx.send(embed=embed)


@bot.command(name="market", help="Quick snapshot of major indices.")
async def cmd_market(ctx):
    """Post a single embed with live data for five major indices."""
    indices = {
        "S&P 500":         "^GSPC",
        "Dow Jones":       "^DJI",
        "NASDAQ":          "^IXIC",
        "Russell 2000":    "^RUT",
        "VIX (Fear Index)":"^VIX",
    }

    embed = discord.Embed(
        title="\U0001f4ca Market Snapshot",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow(),
    )

    async with ctx.typing():
        lines = []
        for label, sym in indices.items():
            info       = yf.Ticker(sym).info
            price      = info.get("regularMarketPrice") or info.get("currentPrice")
            prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
            if price and prev_close:
                pct   = (price - prev_close) / prev_close * 100
                arrow = "\u25b2" if pct >= 0 else "\u25bc"
                sign  = "+" if pct >= 0 else ""
                lines.append(f"**{label}** (`{sym}`)\n{arrow} {price:,.2f}  ({sign}{pct:.2f}%)")
            else:
                lines.append(f"**{label}** — data unavailable")

    embed.description = "\n\n".join(lines)
    embed.set_footer(text="Data via Yahoo Finance  |  prices may be delayed up to 15 min")
    await ctx.send(embed=embed)


@bot.command(name="stockhelp", help="List all stock-bot commands.")
async def cmd_stockhelp(ctx):
    embed = discord.Embed(title="Stock Bot  \u2014  Available Commands", color=discord.Color.gold())
    embed.add_field(
        name="`!stock <TICKER>`",
        value="Full summary for one stock.\nExample: `!stock TSLA`",
        inline=False,
    )
    embed.add_field(
        name="`!compare <T1> <T2> [T3]`",
        value="Compare 2-3 stocks.\nExample: `!compare AAPL MSFT GOOG`",
        inline=False,
    )
    embed.add_field(
        name="`!market`",
        value="Live snapshot of the S&P 500, Dow, NASDAQ, Russell 2000, and VIX.",
        inline=False,
    )
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Unknown command. Use `!stockhelp` to see what is available.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`.  Try `!stockhelp`.")
    else:
        await ctx.send(f"An unexpected error occurred: {error}")
        raise error


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError(
            "DISCORD_TOKEN is not set.\n"
            "Run:  export DISCORD_TOKEN=\"paste-your-token-here\""
        )
    bot.run(TOKEN)
