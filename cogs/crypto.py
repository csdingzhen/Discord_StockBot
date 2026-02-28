"""
crypto.py — Cryptocurrency price commands.
Commands: !crypto, !btc
"""
import discord
from discord.ext import commands

from services.market_data import get_ticker_info
from utils.formatters import change_emoji, price_color, make_embed
from utils.constants import CRYPTO_TICKERS


class Crypto(commands.Cog):
    """Cryptocurrency price commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="crypto", help="Snapshot of top cryptocurrencies.")
    async def cmd_crypto(self, ctx):
        embed = make_embed("\U0001f0cf Crypto Snapshot", color=discord.Color.dark_gold())
        async with ctx.typing():
            lines = []
            for name, sym in CRYPTO_TICKERS.items():
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

    @commands.command(name="btc", help="Quick Bitcoin price check.")
    async def cmd_btc(self, ctx):
        async with ctx.typing():
            info       = get_ticker_info("BTC-USD")
            price      = info.get("regularMarketPrice") or info.get("currentPrice")
            prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if price and prev_close:
            pct  = (price - prev_close) / prev_close * 100
            icon = change_emoji(pct)
            sign = "+" if pct >= 0 else ""
            embed = make_embed(f"{icon} Bitcoin (BTC-USD)", color=price_color(pct))
            embed.add_field(name="Price",  value=f"${price:,.2f}",      inline=True)
            embed.add_field(name="Change", value=f"{sign}{pct:.2f}%",   inline=True)
            await ctx.send(embed=embed)
        else:
            await ctx.send("Could not fetch Bitcoin data.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Crypto(bot))
