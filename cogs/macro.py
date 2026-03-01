"""
macro.py — Macro dashboard command.
Usage: !macro

Displays five sections as a Discord embed:
  📦 核心资产  — BTC, Gold, Silver, WTI (price + % change)
  ⚖️ 比价     — Gold/Silver ratio, Gold/Oil ratio
  💧 流动性   — 10Y TIPS real yield, Fed reverse repo
  🏦 债市     — US 10Y/2Y, Japan 10Y, spreads, inversion status
  🚨 风险     — DXY, VIX, USD/CNH
"""
import asyncio

import discord
from discord.ext import commands

from services.macro_data import fetch_macro_snapshot
from utils.formatters import make_embed
from utils.constants import MACRO_ITEM_LABELS, VIX_PANIC_THRESHOLD, VIX_ELEVATED_THRESHOLD


# ------------------------------------------------------------------
# Shared formatting helpers
# ------------------------------------------------------------------

def _arrow(v) -> str:
    if v is None:
        return "➡️"
    return "▲" if v > 0 else ("▼" if v < 0 else "➡️")


def _fmt_price(price: float) -> str:
    if price is None:
        return "N/A"
    if price >= 10_000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:.4f}"


def _pct(v) -> str:
    """Percentage change string, e.g. (+2.31%)"""
    if v is None:
        return ""
    return f"({'+'if v >= 0 else ''}{v:.2f}%)"


def _bp(v) -> str:
    """Change in base point """
    if v is None:
        return ""
    return f"({'+'if v >= 0 else ''}{v*100:.0f}bp)"


def _label(key: str) -> str:
    return MACRO_ITEM_LABELS.get(key, key)


# ------------------------------------------------------------------
# Section renderers
# ------------------------------------------------------------------

def _core_assets(data: dict) -> str:
    lines = []
    for key, d in data.items():
        price = d.get("price")
        pct   = d.get("pct_change")
        if price is None:
            lines.append(f"**{_label(key)}** — N/A")
        else:
            lines.append(
                f"**{_label(key)}**  {_arrow(pct)} {_fmt_price(price)}  {_pct(pct)}"
            )
    return "\n".join(lines)


def _ratios(data: dict) -> str:
    lines = []
    for key, d in data.items():
        val = d.get("value")
        lines.append(
            f"**{_label(key)}**  {val:.1f}" if val is not None
            else f"**{_label(key)}** — N/A"
        )
    return "\n".join(lines)


def _liquidity(data: dict) -> str:
    lines = []
    for key, d in data.items():
        val = d.get("value")
        chg = d.get("change")
        if val is None:
            lines.append(f"**{_label(key)}** — N/A")
            continue
        if key == "RRP":
            # RRPONTSYD is in billions USD
            chg_s = f"  ({'+'if chg >= 0 else ''}{chg:.1f}B)" if chg is not None else ""
            lines.append(f"**{_label(key)}**  {_arrow(chg)} ${val:,.0f}B{chg_s}")
        else:
            # TIPS real yield: show in % with pp change
            lines.append(
                f"**{_label(key)}**  {_arrow(chg)} {val:.2f}%  {_bp(chg)}"
            )
    return "\n".join(lines)


def _bonds(data: dict) -> str:
    lines = []
    for key, d in data.items():
        val      = d.get("value")
        chg      = d.get("change")
        inverted = d.get("inverted", False)
        if val is None:
            lines.append(f"**{_label(key)}** — N/A")
            continue
        if key == "USJPSpread":
            # Derived spread — no daily change available
            lines.append(f"**{_label(key)}**  {val*100:.0f}bp")
        elif key == "Spread10Y2Y":
            inv_note = "  ⚠️ 倒挂中" if inverted else "  ✅ 正常"
            lines.append(
                f"**{_label(key)}**  {_arrow(chg)} {val:.2f}%  {_bp(chg)}{inv_note}"
            )
        else:
            lines.append(
                f"**{_label(key)}**  {_arrow(chg)} {val:.2f}%  {_bp(chg)}"
            )
    return "\n".join(lines)


def _risk(data: dict) -> str:
    lines = []
    for key, d in data.items():
        price = d.get("price")
        pct   = d.get("pct_change")
        if price is None:
            lines.append(f"**{_label(key)}** — N/A")
            continue
        extra = ""
        if key == "VIX":
            if price >= VIX_PANIC_THRESHOLD:
                extra = "  🔴 PANIC"
            elif price >= VIX_ELEVATED_THRESHOLD:
                extra = "  ⚠️ Elevated"
            else:
                extra = "  ✅ Normal"
        lines.append(
            f"**{_label(key)}**  {_arrow(pct)} {_fmt_price(price)}  {_pct(pct)}{extra}"
        )
    return "\n".join(lines)


# ------------------------------------------------------------------
# Cog
# ------------------------------------------------------------------

class Macro(commands.Cog):
    """Macro-economic dashboard."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="macro", help="宏观仪表盘 — core assets, ratios, liquidity, bonds, risk.")
    async def cmd_macro(self, ctx):
        async with ctx.typing():
            snapshot = await asyncio.to_thread(fetch_macro_snapshot)

        embed = make_embed("🌐 Macro Dashboard 宏观仪表盘", color=discord.Color.dark_gold())

        embed.add_field(
            name="📦 核心资产 Core Assets",
            value=_core_assets(snapshot["core_assets"]) or "N/A",
            inline=False,
        )
        embed.add_field(
            name="⚖️ 比价 Ratios",
            value=_ratios(snapshot["ratios"]) or "N/A",
            inline=False,
        )
        embed.add_field(
            name="💧 流动性 Liquidity",
            value=_liquidity(snapshot["liquidity"]) or "N/A",
            inline=False,
        )
        embed.add_field(
            name="🏦 债市 Bond Rates",
            value=_bonds(snapshot["bonds"]) or "N/A",
            inline=False,
        )
        embed.add_field(
            name="🚨 风险 Risk",
            value=_risk(snapshot["risk"]) or "N/A",
            inline=False,
        )

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Macro(bot))
