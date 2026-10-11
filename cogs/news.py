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
import logging
from datetime import datetime, time, timezone

import discord
from discord.ext import commands, tasks

import config
from cogs.scheduler import market_open_today
from services import llm_client
from services.jin10_mcp import Jin10Error, Jin10MCPClient
from storage import jin10_store
from utils.market_time import ET

_log = logging.getLogger(__name__)
_UNCLASSIFIED = "未AI分类"

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
    content = item["content"][:3500]
    if len(item["content"]) > 3500:
        content += "\n（原文较长，以下链接可查看全文）"
    if item.get("url"):
        content += f"\n\n[查看原文]({item['url'][:400]})"
    embed = discord.Embed(description=content, color=color)
    if ai_block:
        embed.add_field(name="AI 解读", value=ai_block[:1024], inline=False)
    embed.set_footer(text=f"Jin10 快讯 | {item.get('time', '')}")
    return embed


class News(commands.Cog):
    """Jin10 flash-news polling, classification, and delivery."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        jin10_store.init_db()
        self._consecutive_failures = 0
        self._failure_alerted = False
        self._last_fetch_error = ""
        self._last_fetch_success = None
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
        try:
            await self._poll_once()
        except Exception:
            _log.exception("News processing failed; next poll remains enabled")

    async def _poll_once(self):
        if not config.JIN10_API_KEY:
            return
        try:
            async with Jin10MCPClient(config.JIN10_API_KEY) as client:
                new_items = await self._fetch_new_items(client)
        except Exception as e:
            self._last_fetch_error = str(e) if isinstance(e, Jin10Error) else type(e).__name__
            _log.warning("[news] Jin10 fetch failed: %s", self._last_fetch_error)
            await self._record_poll_failure()
            return
        was_alerted = self._failure_alerted
        self._record_poll_success()
        if was_alerted:
            await self._post_recovery_notice()

        for item in jin10_store.get_pending_l3_items():
            try:
                await self._post_l3_alert(item, item["category"])
            except Exception:
                _log.exception("L3 delivery failed; retained for next poll")

        # Already oldest-first from _fetch_new_items.
        classification_available = True
        for item in new_items:
            if _is_pre_summarized_roundup(item["content"]):
                try:
                    await self._post_roundup(item)
                except Exception:
                    _log.exception("Roundup delivery failed")
                continue

            result = {"level": 2, "category": _UNCLASSIFIED}
            if classification_available:
                try:
                    result = await llm_client.classify_flash(item["content"])
                except Exception as e:
                    _log.warning("News LLM classification failed; queueing raw news: %s", e)
                    classification_available = False

            # L2 is a delivery queue here, not an AI importance claim when unclassified.
            jin10_store.save_classified_item(item, result["level"], result["category"])
            if result["level"] == 3:
                try:
                    await self._post_l3_alert(item, result["category"])
                except Exception:
                    _log.exception("L3 delivery failed; retained for next poll")

    @poll_flash.before_loop
    async def before_poll_flash(self):
        await self.bot.wait_until_ready()

    async def _fetch_new_items(self, client: Jin10MCPClient) -> list[dict]:
        """Newest-first pages, paged backward via cursor until hitting an
        already-seen item, has_more is false, or the page cap is hit.
        Returns unseen items oldest-first, ready to process in publish order.
        """
        collected = []
        urls = set()
        cursor = None
        for _ in range(_MAX_PAGES_PER_POLL):
            data = await client.list_flash(cursor)
            hit_seen = False
            for item in data["items"]:
                if jin10_store.is_seen(item["url"]):
                    hit_seen = True
                    continue
                if item["url"] not in urls:
                    collected.append(item)
                    urls.add(item["url"])
            if hit_seen or not data.get("has_more") or not data.get("next_cursor"):
                break
            cursor = data["next_cursor"]
        return list(reversed(collected))

    async def _record_poll_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAILURE_ALERT_THRESHOLD and not self._failure_alerted:
            self._failure_alerted = await self._post_outage_warning()

    def _record_poll_success(self):
        self._consecutive_failures = 0
        self._failure_alerted = False
        self._last_fetch_success = datetime.now(timezone.utc).isoformat()

    async def _post_outage_warning(self):
        channel_id = config.ALERT_CHANNEL_ID
        if not channel_id:
            return False
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return False
        embed = discord.Embed(
            title="⚠️ Jin10 快讯获取连续失败",
            description=(
                f"连续 {self._consecutive_failures} 次金十获取失败（不包含 LLM 错误）。\n"
                f"错误类型：{self._last_fetch_error}\n"
                f"最近成功（UTC）：{self._last_fetch_success or '本次启动后尚未成功'}\n"
                "401/403 请检查金十凭据与权限；429 检查限流；其他错误请查看 Railway 日志。"
            ),
            color=discord.Color.orange(),
        )
        await channel.send(embed=embed)
        return True

    async def _post_recovery_notice(self):
        channel = self.bot.get_channel(config.ALERT_CHANNEL_ID) if config.ALERT_CHANNEL_ID else None
        if channel:
            try:
                await channel.send(embed=discord.Embed(
                    title="Jin10 快讯获取已恢复", color=discord.Color.green(),
                    description="数据源连接已恢复；AI 处理状态与此独立。",
                ))
            except Exception:
                _log.exception("Failed to deliver Jin10 recovery notice")

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
                jin10_store.mark_digested([item["url"]])
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
        if ai_block is None:
            embed.set_footer(text=f"Jin10 快讯 | {llm_client.FAILURE_NOTICE}")
        embed.set_author(name=f"⚠️ 重大快讯 | {category}")
        await channel.send(embed=embed)
        jin10_store.mark_l3_posted(item["url"])

    async def _post_roundup(self, item: dict):
        """Jin10's own pre-aggregated roundup -- post as-is, no classification/AI call."""
        channel_id = config.NEWS_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        embed = _flash_embed(item, discord.Color.gold())
        embed.set_author(name="📋 金十汇总")
        await channel.send(embed=embed)
        jin10_store.save_classified_item(item, level=2, category="每日汇总")
        jin10_store.mark_digested([item["url"]])

    # ------------------------------------------------------------------
    # Digest: roll up pending L2 items at scheduled times
    # ------------------------------------------------------------------

    @tasks.loop(time=_DIGEST_TIMES)
    async def flush_digest(self):
        if not market_open_today():
            return
        try:
            await self._post_l2_digest()
        except Exception:
            _log.exception("News digest delivery failed; pending items retained")

    @flush_digest.before_loop
    async def before_flush_digest(self):
        await self.bot.wait_until_ready()

    async def _post_l2_digest(self):
        pending = jin10_store.get_pending_l2_items(limit=100)
        if not pending:
            return

        channel_id = config.NEWS_CHANNEL_ID
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return

        # Bound each prompt and message; backlog beyond 100 stays queued.
        for start in range(0, len(pending), 20):
            batch = pending[start:start + 20]
            try:
                summary = await llm_client.summarize_flash_digest(batch)
            except Exception as e:
                _log.warning("News digest LLM failed; sending source excerpts: %s", e)
                await self._post_raw_digest(channel, batch)
                continue
            embed = discord.Embed(
                title=f"📰 市场快讯摘要（{len(batch)}条）",
                description=summary[:4096], color=discord.Color.blue(),
            )
            note = " | 含未AI分级快讯" if any(i["category"] == _UNCLASSIFIED for i in batch) else ""
            embed.set_footer(text=f"Jin10 快讯摘要{note}")
            await channel.send(embed=embed)
            jin10_store.mark_digested([item["url"] for item in batch])

    async def _post_raw_digest(self, channel, items):
        lines, urls = [], []
        for item in items:
            excerpt = item["content"][:240].replace("\n", " ")
            if len(item["content"]) > 240:
                excerpt += "..."
            line = f"[{item['time']}] {excerpt}\n[原文]({item['url'][:400]})"
            if lines and len("\n\n".join(lines + [line])) > 3800:
                await self._send_raw_page(channel, lines, urls)
                lines, urls = [], []
            lines.append(line)
            urls.append(item["url"])
        if lines:
            await self._send_raw_page(channel, lines, urls)

    async def _send_raw_page(self, channel, lines, urls):
        embed = discord.Embed(
            title="市场快讯原文摘录（非 AI 摘要）", description="\n\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Jin10 | {llm_client.FAILURE_NOTICE}")
        await channel.send(embed=embed)
        jin10_store.mark_digested(urls)

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
