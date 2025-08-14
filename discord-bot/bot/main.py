# bot/main.py
from __future__ import annotations
import logging
import asyncio
import discord
from discord.ext import commands

from .config import settings
from .db import init_db

# <- Alle Cogs, die du aktuell im Projekt hast
EXTENSIONS = [
    "bot.cogs.admin",
    "bot.cogs.autorole",
    "bot.cogs.cleanup",
    "bot.cogs.features",
    "bot.cogs.guild_join",
    "bot.cogs.moderation",
    "bot.cogs.vc_tracking_override",
    "bot.cogs.vc_tracking_simple",
    "bot.cogs.welcome_leave",
    "bot.cogs.events",  # dein AppCommand-Error-Handler etc.
]

# Basic logging (Railway-Logs)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("discord-bot")

class FazzerBot(commands.Bot):
    async def setup_hook(self):
        # 1) DB initialisieren (optional, wenn DATABASE_URL gesetzt ist)
        try:
            await init_db()
            log.info("DB initialisiert (oder übersprungen, wenn keine DATABASE_URL).")
        except Exception as e:
            log.exception(f"DB-Initialisierung fehlgeschlagen: {e}")

        # 2) Cogs laden
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info(f"Cog geladen: {ext}")
            except Exception as e:
                log.exception(f"Fehler beim Laden von {ext}: {e}")

        # 3) Slash-Commands sync (global)
        try:
            synced = await self.tree.sync()
            log.info(f"Slash-Commands synchronisiert ({len(synced)} Kommandos).")
        except Exception as e:
            log.exception(f"Slash-Command-Sync fehlgeschlagen: {e}")

        # 4) Presence setzen
        try:
            await self.change_presence(
                activity=discord.Activity(type=discord.ActivityType.listening, name="/help • /features"),
                status=discord.Status.online,
            )
        except Exception:
            pass

    async def on_ready(self):
        log.info(f"✅ Eingeloggt als {self.user} (ID: {self.user.id})")

def run_bot():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True  # benötigt für Autorole/Welcome/Leave

bot = FazzerBot(command_prefix="!", intents=intents)

if not settings.token:
    raise RuntimeError("DISCORD_TOKEN fehlt. Bitte in Railway unter Variables setzen.")
bot.run(settings.token)