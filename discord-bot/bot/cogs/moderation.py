from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from ..utils.checks import require_manage_channels
from ..utils.replies import reply_text

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="lock", description="Sperrt Kanäle für X Minuten ab Zeitpunkt HH:MM")
    @require_manage_channels()
    @app_commands.describe(
        start_time="Startzeit im Format HH:MM (Server-Zeit)",
        duration="Dauer in Minuten",
    )
    async def lock(self, interaction: discord.Interaction,
                   channels: Optional[discord.TextChannel],
                   start_time: str, duration: int):
        # TODO: Portiere deine alte !lock-Logik (inkl. Greedy-Channel-Liste) hierhin.
        await reply_text(interaction, "🔒 Lock (TODO)")

    @app_commands.command(name="unlock", description="Hebt die Sperre sofort auf")
    @require_manage_channels()
    async def unlock(self, interaction: discord.Interaction,
                     channels: Optional[discord.TextChannel]):
        # TODO: Portiere deine alte !unlock-Logik (inkl. Greedy-Channel-Liste) hierhin.
        await reply_text(interaction, "🔓 Unlock (TODO)")

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
