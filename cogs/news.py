"""
news.py — Jin10 flash-news pipeline: poll, classify, and deliver via Discord.

L1 (low importance)  -> stored only, never posted.
L2 (moderate)         -> queued; rolled into one AI-summarized digest at
                          scheduled times (premarket / hourly during market
                          hours / postmarket) instead of posted individually.
L3 (high importance)  -> posted immediately as a full AI-analysis alert.

Polling runs continuously (Jin10 covers global news, not just US hours);
the digest schedule is gated to NYSE trading days, matching the existing
premarket/market-summary tasks in scheduler.py.
"""
from datetime import time

import discord
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo

import config
from cogs.scheduler import market_open_today
from services import llm_client
from services.jin10_mcp import Jin10MCPClient
from storage import jin10_store

ET = ZoneInfo("America/New_York")

# Premarket, hourly through the trading day, then a post-market wrap.
_DIGEST_TIMES = [
    time(9, 0, tzinfo=ET),
    time(10, 30, tzinfo=ET),
    time(11, 30, tzinfo=ET),
    time(12, 30, tzinfo=ET),
    time(13, 30, tzinfo=ET),
    time(14, 30, tzinfo=ET),
    time(15, 30, tzinfo=ET),
    time(16, 5, tzinfo=ET),
]


def _flash_embed(item: dict, color: discord.Color, ai_block: str | None = None) -> discord.Embed:
    content = item["content"]
    title = content[:80] + "…" if len(content) > 80 else None
    embed = discord.Embed(title=title, description=content, color=color)
    if item.get("url"):
        embed.url = item["url"]
    if ai_block:
        embed.add_field(name="AI 解读", value=ai_block, inline=False)
    embed.set_footer(text=f"Jin10 快讯 | {item.get('time', '')}")
    return embed


class News(commands.Cog):
    """Jin10 flash-news polling, classification, and delivery."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        jin10_store.init_db()
        self.poll_flash.start()
        self.flush_digest.start()

    def cog_unload(self):
        self.poll_flash.cancel()
        self.flush_digest.cancel()

    # ------------------------------------------------------------------
    # Polling: fetch, classify, store, and immediately alert on L3
    # ------------------------------------------------------------------

    @tasks.loop(minutes=3)
    async def poll_flash(self):
        if not config.JIN10_API_KEY:
            return
        try:
            async with Jin10MCPClient(config.JIN10_API_KEY) as client:
                data = await client.list_flash()
        except Exception as e:
            print(f"[news] Jin10 fetch failed: {e}")
            return

        # Oldest first so classification/posting order matches publish order.
        for item in reversed(data["items"]):
            if jin10_store.is_seen(item["url"]):
                continue
            try:
                result = await llm_client.classify_flash(item["content"])
            except Exception as e:
                print(f"[news] classify failed, will retry next poll: {e}")
                continue

            jin10_store.save_classified_item(item, result["level"], result["category"])

            if result["level"] == 3:
                await self._post_l3_alert(item, result["category"])

    @poll_flash.before_loop
    async def before_poll_flash(self):
        await self.bot.wait_until_ready()

    async def _post_l3_alert(self, item: dict, category: str):
        channel_id = config.ALERT_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        try:
            ai_block = await llm_client.summarize_flash_alert(item["content"])
        except Exception as e:
            print(f"[news] L3 summarize failed: {e}")
            ai_block = None

        embed = _flash_embed(item, discord.Color.red(), ai_block)
        embed.set_author(name=f"⚠️ 重大快讯 | {category}")
        await channel.send(embed=embed)
        jin10_store.mark_l3_posted(item["url"])

    # ------------------------------------------------------------------
    # Digest: roll up pending L2 items at scheduled times
    # ------------------------------------------------------------------

    @tasks.loop(time=_DIGEST_TIMES)
    async def flush_digest(self):
        if not market_open_today():
            return
        await self._post_l2_digest()

    @flush_digest.before_loop
    async def before_flush_digest(self):
        await self.bot.wait_until_ready()

    async def _post_l2_digest(self):
        pending = jin10_store.get_pending_l2_items()
        if not pending:
            return

        channel_id = config.NEWS_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        try:
            summary = await llm_client.summarize_flash_digest(pending)
        except Exception as e:
            print(f"[news] digest summarize failed: {e}")
            return

        embed = discord.Embed(
            title=f"📰 市场快讯摘要（{len(pending)}条）",
            description=summary,
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Jin10 快讯摘要")
        await channel.send(embed=embed)
        jin10_store.mark_digested([item["url"] for item in pending])

    # ------------------------------------------------------------------
    # Dev: manual trigger for testing
    # ------------------------------------------------------------------

    @commands.command(name="newsdigest", hidden=True)
    @commands.is_owner()
    async def force_digest(self, ctx):
        """Owner-only: manually fire the L2 digest flush right now."""
        await self._post_l2_digest()
        await ctx.send("Digest flush attempted (no-op if nothing pending).", delete_after=5)


async def setup(bot: commands.Bot):
    await bot.add_cog(News(bot))
