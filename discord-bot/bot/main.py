import asyncio
import logging
import discord
from discord.ext import commands
from .config import settings
from .db import init_db
from pathlib import Path

logging.basicConfig(level=logging.INFO)

COGS = [
    "bot.cogs.events",
    "bot.cogs.features",
    "bot.cogs.admin",
    "bot.cogs.moderation",
    "bot.cogs.maintenance",
]

def make_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    intents.guilds = True
    intents.voice_states = True

    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
        help_command=None  # optional: custom help
    )
    return bot

async def _async_main():
    # Initialize DB (creates tables if needed)
    await init_db()

    bot = make_bot()

    @bot.event
    async def setup_hook():
        # Load cogs
        for ext in COGS:
            try:
                await bot.load_extension(ext)
                logging.info("Loaded cog: %s", ext)
            except Exception as e:
                logging.exception("Failed to load %s: %s", ext, e)
        # Sync slash commands (global). For faster iteration, replace with per-guild sync.
        try:
            await bot.tree.sync()
            logging.info("Synced slash commands globally.")
        except Exception as e:
            logging.exception("Slash sync failed: %s", e)

    await bot.start(settings.token)

def run_bot():
    asyncio.run(_async_main())
