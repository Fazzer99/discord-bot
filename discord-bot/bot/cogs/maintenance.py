import discord
from discord import app_commands
from discord.ext import commands
from ..utils.replies import reply_text
from ..utils.checks import require_manage_channels

class MaintenanceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="cleanup", description="Startet periodisches Cleanup (TODO)")
    @require_manage_channels()
    async def cleanup(self, interaction: discord.Interaction):
        # TODO: Port aus deinem alten !cleanup
        await reply_text(interaction, "🧹 Cleanup gestartet (TODO)")

    @app_commands.command(name="cleanup_stop", description="Stoppt das Cleanup (TODO)")
    @require_manage_channels()
    async def cleanup_stop(self, interaction: discord.Interaction):
        # TODO: Port aus deinem alten !cleanup_stop
        await reply_text(interaction, "🛑 Cleanup gestoppt (TODO)")

async def setup(bot: commands.Bot):
    await bot.add_cog(MaintenanceCog(bot))
