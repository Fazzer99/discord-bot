# bot/cogs/events.py
from __future__ import annotations
import discord
from discord.ext import commands
from discord import app_commands
from ..utils.replies import reply_error

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            return await reply_error(interaction, "❌ Dir fehlen die nötigen Berechtigungen.")
        if isinstance(error, app_commands.CheckFailure):
            return await reply_error(interaction, "❌ Check fehlgeschlagen (Rechte/Setup).")
        # default: loggen und kurze Meldung
        try:
            await reply_error(interaction, "❌ Unerwarteter Fehler beim Ausführen des Befehls.")
        except Exception:
            pass
        raise error  # damit du im Log die Traceback siehst

async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))