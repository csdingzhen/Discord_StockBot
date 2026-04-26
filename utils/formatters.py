from datetime import datetime
import discord
import config


def format_large_number(n) -> str:
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


def change_emoji(pct: float) -> str:
    """Return a directional emoji based on the percentage change."""
    if pct > 2:
        return "\U0001f680"   # rocket
    if pct > 0:
        return "\U0001f4c8"   # chart up
    if pct < -2:
        return "\U0001f534"   # red circle
    if pct < 0:
        return "\U0001f4c9"   # chart down
    return "\u27a1\ufe0f"     # right arrow


def price_color(change_pct: float) -> discord.Color:
    """Return green for positive, red for negative."""
    return discord.Color.green() if change_pct >= 0 else discord.Color.red()


def beat_miss_str(actual, estimated) -> str:
    """Return a beat/miss label with percentage, or empty string if data is missing."""
    if actual is None or estimated is None or estimated == 0:
        return ""
    pct = (actual - estimated) / abs(estimated) * 100
    if pct > 0:
        return f"✅ Beat (+{pct:.1f}%)"
    if pct < 0:
        return f"❌ Miss ({pct:.1f}%)"
    return "➡️ In-line"


def timing_label(time_code: str) -> str:
    """Convert FMP time codes to display labels."""
    return {"bmo": "盘前 BMO", "amc": "盘后 AMC", "dmh": "盘中 DMH"}.get(
        (time_code or "").lower(), time_code or "—"
    )


def make_embed(
    title: str,
    description: str = "",
    color: discord.Color = discord.Color.blurple(),
) -> discord.Embed:
    """Create a Discord Embed pre-filled with timestamp and footer."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=config.DATA_FOOTER)
    return embed
