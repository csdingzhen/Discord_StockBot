import asyncio
import math
import time

import discord
from discord.ext import commands, tasks
import config
from services.metrics import (
    discord_api_latency_seconds,
    discord_command_duration_seconds,
    discord_events_total,
    start_metrics_server,
)

COGS = [
    "cogs.stocks",
    "cogs.crypto",
    "cogs.commodities",
    "cogs.options",
    "cogs.options_flow",
    "cogs.earnings",
    "cogs.macro",
    "cogs.scheduler",
    "cogs.analysis",
    "cogs.news",
]

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)


@bot.event
async def on_ready():
    discord_events_total.labels(type="ready").inc()
    print(f"Logged in as {bot.user}  (ID: {bot.user.id})")
    print(f"Loaded cogs: {', '.join(COGS)}")


@bot.event
async def on_message(message: discord.Message):
    # Count every message the bot sees, then hand off to command processing.
    # Overriding on_message means we must call process_commands ourselves or
    # commands stop firing.
    if message.author != bot.user:
        discord_events_total.labels(type="message").inc()
    await bot.process_commands(message)


@bot.event
async def on_command(ctx):
    discord_events_total.labels(type="command_invoked").inc()


@bot.event
async def on_command_completion(ctx):
    discord_events_total.labels(type="command_completed").inc()


@bot.event
async def on_command_error(ctx, error):
    discord_events_total.labels(type="command_error").inc()
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Unknown command. Use `!stockhelp` to see available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`. Try `!stockhelp`.")
    else:
        await ctx.send(f"An unexpected error occurred: {error}")
        raise error


# ---------------------------------------------------------------------------
# Command timing — global before/after hooks avoid touching every cog.
# after_invoke fires whether or not the command raised (as long as it started),
# so this captures duration for both success and in-command errors.
# ---------------------------------------------------------------------------

@bot.before_invoke
async def _before_command(ctx):
    ctx.command_start = time.perf_counter()


@bot.after_invoke
async def _after_command(ctx):
    start = getattr(ctx, "command_start", None)
    if start is not None and ctx.command is not None:
        discord_command_duration_seconds.labels(
            command=ctx.command.qualified_name
        ).observe(time.perf_counter() - start)


# ---------------------------------------------------------------------------
# Gateway latency — sampled periodically into a gauge.
# ---------------------------------------------------------------------------

@tasks.loop(seconds=15)
async def _update_latency():
    latency = bot.latency  # seconds; NaN until the first heartbeat
    if not math.isnan(latency) and math.isfinite(latency):
        discord_api_latency_seconds.set(latency)


@_update_latency.before_loop
async def _before_latency_loop():
    await bot.wait_until_ready()


async def main():
    start_metrics_server()
    _update_latency.start()
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"  [OK] {cog}")
            except Exception as e:
                print(f"  [FAIL] {cog}: {e}")
        await bot.start(config.DISCORD_TOKEN)


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise ValueError("DISCORD_TOKEN is not set. Add it to your .env file.")
    asyncio.run(main())
