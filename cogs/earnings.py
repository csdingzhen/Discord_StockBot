"""
earnings.py — Earnings commands.
Commands: !earnings <TICKER>
"""
import discord
from discord.ext import commands

from services.earnings_data import fetch_ticker_earnings
from utils.formatters import beat_miss_str, format_large_number, make_embed


class Earnings(commands.Cog):
    """Earnings-related commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="earnings", help="Earnings info for a ticker. Usage: !earnings AAPL")
    async def cmd_earnings(self, ctx, ticker: str = None):
        if ticker is None:
            await ctx.send("Please provide a ticker.  Example: `!earnings AAPL`")
            return
        ticker = ticker.upper()
        async with ctx.typing():
            upcoming, recent = fetch_ticker_earnings(ticker)
            if upcoming is None and recent is None:
                await ctx.send(f"No earnings data found for **{ticker}**.")
                return

            embed = make_embed(f"📅 Earnings: {ticker}", color=discord.Color.orange())

            if upcoming:
                eps_e = upcoming.get("epsEstimated")
                rev_e = upcoming.get("revenueEstimated")
                lines = [
                    f"📆 **{upcoming.get('date', 'N/A')}**",
                    f"EPS 预期:     **${eps_e if eps_e is not None else 'N/A'}**",
                    f"Revenue 预期: **{format_large_number(rev_e)}**",
                ]
                embed.add_field(name="📊 即将发布 Upcoming", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="📊 即将发布 Upcoming", value="暂无数据 —", inline=False)

            if recent:
                eps_a = recent.get("epsActual")
                eps_e = recent.get("epsEstimated")
                rev_a = recent.get("revenueActual")
                rev_e = recent.get("revenueEstimated")
                lines = [
                    f"📆 **{recent.get('date', 'N/A')}**",
                    f"EPS:     **${eps_a}** vs ${eps_e} est  {beat_miss_str(eps_a, eps_e)}",
                    f"Revenue: **{format_large_number(rev_a)}** vs {format_large_number(rev_e)} est  {beat_miss_str(rev_a, rev_e)}",
                ]
                embed.add_field(name="📋 最近财报 Last Report", value="\n".join(lines), inline=False)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Earnings(bot))
