import asyncio
import discord
from discord.ext import commands
import config

COGS = [
    "cogs.stocks",
    "cogs.crypto",
    "cogs.commodities",
    "cogs.options",
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
    print(f"Logged in as {bot.user}  (ID: {bot.user.id})")
    print(f"Loaded cogs: {', '.join(COGS)}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Unknown command. Use `!stockhelp` to see available commands.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`. Try `!stockhelp`.")
    else:
        await ctx.send(f"An unexpected error occurred: {error}")
        raise error


async def main():
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
