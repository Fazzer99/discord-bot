# bot/cogs/events.py
from __future__ import annotations
import logging
import discord
from discord.ext import commands
from discord import app_commands
from ..utils.replies import reply_error, reply_text

log = logging.getLogger("discord-bot")

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        # 1) Globale Check-Fehler (z. B. Sprach-Guard) NICHT spammen:
        #    Der Check selbst sendet bereits ein Embed → hier einfach leise abbrechen.
        if isinstance(error, app_commands.CheckFailure):
            return

        # 2) Fehlende Rechte
        if isinstance(error, app_commands.MissingPermissions):
            await reply_error(interaction, "❌ Dir fehlen die nötigen Berechtigungen.", ephemeral=True)
            return

        # 3) Cooldown freundlich erklären (falls du sowas nutzt)
        if isinstance(error, app_commands.CommandOnCooldown):
            await reply_text(
                interaction,
                f"⏳ Bitte warte noch {error.retry_after:.1f} Sek., bevor du den Befehl erneut nutzt.",
                kind="warning",
                ephemeral=True,
            )
            return

        # 4) Unerwartete Fehler: für den User kurz & für uns geloggt
        cmd_name = getattr(getattr(interaction, "command", None), "name", "?")
        log.exception(f"Slash-Command-Error in /{cmd_name}: {error}")
        try:
            await reply_error(interaction, "❌ Unerwarteter Fehler beim Ausführen des Befehls.", ephemeral=True)
        except Exception:
            pass  # falls die Response schon abgeschlossen ist etc.

async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))