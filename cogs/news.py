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

# Jin10 itself publishes pre-aggregated roundups (e.g. "每日科技要闻速递", or a
# single flash with "国内新闻：" / "国际新闻：" sections listing many unrelated
# items). These are already a human-edited summary of multiple stories, not
# one atomic event -- running them through single-event classification and
# AI analysis misrepresents them (wrong category, and the long multi-bullet
# text duplicates badly into a synthesized title). Detect and post as-is.
_ROUNDUP_TITLE_MARKERS = ("速递", "要闻", "导读", "早报", "晚报", "周报", "晨报")
_ROUNDUP_SECTION_HEADERS = ("国内新闻：", "国际新闻：")


def _is_pre_summarized_roundup(content: str) -> bool:
    has_title_marker = any(marker in content[:30] for marker in _ROUNDUP_TITLE_MARKERS)
    has_dual_sections = all(header in content for header in _ROUNDUP_SECTION_HEADERS)
    return has_title_marker or has_dual_sections


# list_flash() with no cursor only returns the newest ~20 items. If more than
# one page's worth of flashes lands between two 3-minute polls (e.g. a
# CPI/NFP release), the overflow would otherwise roll past the window and
# never be seen at all -- not even retried. Page backward via cursor until
# hitting an already-seen item, capped so cold start (nothing seen yet)
# doesn't walk all of Jin10's history.
_MAX_PAGES_PER_POLL = 5

# After this many consecutive fetch failures (~15 min at 3-min polling),
# post one warning so an outage doesn't go unnoticed indefinitely.
_FAILURE_ALERT_THRESHOLD = 5

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
    # No title: synthesizing one from content always duplicates the start of
    # the description below it (worse the longer/more multi-part the content
    # is). The category/alert label already shown via set_author() is
    # headline enough.
    content = item["content"]
    if item.get("url"):
        content += f"\n\n[查看原文]({item['url']})"
    embed = discord.Embed(description=content, color=color)
    if ai_block:
        embed.add_field(name="AI 解读", value=ai_block, inline=False)
    embed.set_footer(text=f"Jin10 快讯 | {item.get('time', '')}")
    return embed


class News(commands.Cog):
    """Jin10 flash-news polling, classification, and delivery."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        jin10_store.init_db()
        self._consecutive_failures = 0
        self._failure_alerted = False
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
                new_items = await self._fetch_new_items(client)
        except Exception as e:
            print(f"[news] Jin10 fetch failed: {e}")
            await self._record_poll_failure()
            return
        self._record_poll_success()

        # Already oldest-first from _fetch_new_items.
        for item in new_items:
            if _is_pre_summarized_roundup(item["content"]):
                await self._post_roundup(item)
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

    async def _fetch_new_items(self, client: Jin10MCPClient) -> list[dict]:
        """Newest-first pages, paged backward via cursor until hitting an
        already-seen item, has_more is false, or the page cap is hit.
        Returns unseen items oldest-first, ready to process in publish order.
        """
        collected = []
        cursor = None
        for _ in range(_MAX_PAGES_PER_POLL):
            data = await client.list_flash(cursor)
            hit_seen = False
            for item in data["items"]:
                if jin10_store.is_seen(item["url"]):
                    hit_seen = True
                    break
                collected.append(item)
            if hit_seen or not data.get("has_more") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        return list(reversed(collected))

    async def _record_poll_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAILURE_ALERT_THRESHOLD and not self._failure_alerted:
            self._failure_alerted = True
            await self._post_outage_warning()

    def _record_poll_success(self):
        self._consecutive_failures = 0
        self._failure_alerted = False

    async def _post_outage_warning(self):
        channel_id = config.ALERT_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return
        minutes = _FAILURE_ALERT_THRESHOLD * 3
        embed = discord.Embed(
            title="⚠️ Jin10 快讯获取连续失败",
            description=(
                f"过去 {_FAILURE_ALERT_THRESHOLD} 次轮询（约 {minutes} 分钟）连续失败，"
                "请检查 JIN10_API_KEY 是否有效或网络连接是否正常。"
            ),
            color=discord.Color.orange(),
        )
        await channel.send(embed=embed)

    async def _post_l3_alert(self, item: dict, category: str):
        recent = jin10_store.get_recent_l3_items(minutes=45, limit=5)
        if recent:
            try:
                is_followup = await llm_client.is_followup_story(
                    item["content"], [r["content"] for r in recent]
                )
            except Exception as e:
                print(f"[news] follow-up check failed, posting anyway: {e}")
                is_followup = False
            if is_followup:
                print(f"[news] suppressing L3 post (follow-up of a recent story): {item['url']}")
                return

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

    async def _post_roundup(self, item: dict):
        """Jin10's own pre-aggregated roundup -- post as-is, no classification/AI call."""
        jin10_store.save_classified_item(item, level=2, category="每日汇总")
        jin10_store.mark_digested([item["url"]])

        channel_id = config.NEWS_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        embed = _flash_embed(item, discord.Color.gold())
        embed.set_author(name="📋 金十汇总")
        await channel.send(embed=embed)

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

    @commands.command(name="recentflash", hidden=True)
    @commands.is_owner()
    async def recent_flash(self, ctx, level: int = None):
        """Owner-only: spot-check recently classified items. !recentflash [1|2|3]"""
        items = jin10_store.get_recent_items(level=level, limit=10)
        if not items:
            await ctx.send("No items found.", delete_after=5)
            return

        lines = []
        for it in items:
            preview = it["content"][:60].replace("\n", " ")
            lines.append(f"`L{it['level']}` [{it['category']}] {it['time']}\n> {preview}")

        embed = discord.Embed(
            title=f"最近 {len(items)} 条快讯" + (f"（Level {level}）" if level else ""),
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(News(bot))
