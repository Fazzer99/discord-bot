# bot/main.py
from __future__ import annotations
import logging
import discord
from discord.ext import commands

from .config import settings
from .db import init_db

# Alle Cogs, die geladen werden sollen
EXTENSIONS = [
    "bot.cogs.admin",
    "bot.cogs.autorole",
    "bot.cogs.cleanup",
    "bot.cogs.events",
    "bot.cogs.features",
    "bot.cogs.guild_join",
    "bot.cogs.moderation",
    "bot.cogs.vc_tracking_override",
    "bot.cogs.vc_tracking_simple",
    "bot.cogs.welcome_leave",
]

# Basic logging (Railway-Logs)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("discord-bot")

class FazzerBot(commands.Bot):
    async def setup_hook(self):
        # 1) DB initialisieren
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

        # 2b) Sprach-Check an ALLE Slash-Commands hängen (kompatibel mit älteren d.py)
        async def _lang_check(interaction: discord.Interaction) -> bool:
            # DMs & /setlang immer erlauben
            if interaction.guild is None:
                return True
            cmd = interaction.command
            if cmd and cmd.name == "setlang":
                return True

            from .services.guild_config import get_guild_cfg
            from .utils.replies import reply_text
            cfg = await get_guild_cfg(interaction.guild.id)
            lang = (cfg.get("lang") or "").lower()
            if lang in ("de", "en"):
                return True

            await reply_text(
                interaction,
                "🌐 Bitte zuerst die Sprache wählen mit `/setlang de` oder `/setlang en`.\n"
                "🌐 Please choose a language first: `/setlang de` or `/setlang en`.",
                kind="warning",
                ephemeral=True,
            )
            # Check must return False (oder Exception werfen). False genügt hier:
            return False

        from discord import app_commands
        for cmd in list(self.tree.get_commands()):
            # Nur AppCommands (Slash), CommandGroups enthalten ebenfalls .checks
            if isinstance(cmd, app_commands.Command):
                if cmd.name != "setlang":
                    # d.py hält Checks in einer Liste; wir fügen unseren hinzu
                    cmd.checks.append(_lang_check)  # type: ignore[attr-defined]
            elif isinstance(cmd, app_commands.Group):
                # Für Gruppen: auch deren Unterbefehle versehen
                for sub in cmd.walk_commands():
                    if sub.name != "setlang":
                        sub.checks.append(_lang_check)  # type: ignore[attr-defined]

        # 3) Slash-Commands synchronisieren
        try:
            TEST_GUILD_ID = None  # z.B. 123456789012345678 für schnelleren Guild-Sync
            if TEST_GUILD_ID:
                synced = await self.tree.sync(guild=discord.Object(id=TEST_GUILD_ID))
            else:
                synced = await self.tree.sync()
            log.info(f"Slash-Commands synchronisiert ({len(synced)} Kommandos).")
        except Exception as e:
            log.exception(f"Slash-Command-Sync fehlgeschlagen: {e}")

        # 4) Presence setzen
        try:
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name="/help • /features"
                ),
                status=discord.Status.online,
            )
        except Exception:
            pass

    async def on_ready(self):
        log.info(f"✅ Eingeloggt als {self.user} (ID: {self.user.id})")

def run_bot():
    # Intents
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True  # benötigt für Autorole/Welcome/Leave

    # Bot erstellen
    bot = FazzerBot(command_prefix="!", intents=intents)

    # Token prüfen & starten
    if not settings.token:
        raise RuntimeError("DISCORD_TOKEN fehlt. Bitte in Railway unter Variables setzen.")
    bot.run(settings.token)