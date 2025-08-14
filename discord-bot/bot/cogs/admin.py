import discord
from discord import app_commands
from discord.ext import commands
from ..utils.checks import require_manage_guild
from ..utils.replies import reply_text

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setlang", description="Setzt die Sprache dieser Guild (de/en)")
    @require_manage_guild()
    async def setlang(self, interaction: discord.Interaction, lang: str):
        # TODO: Portiere deine alte get/update_guild_cfg() Logik hierher
        # Beispiel:
        # await update_guild_cfg(interaction.guild.id, {"lang": lang.lower()})
        await reply_text(interaction, f"✅ Sprache gesetzt auf {lang}.")

    @app_commands.command(name="setup", description="Erstkonfiguration (Kanäle/Rollen etc.)")
    @require_manage_guild()
    async def setup(self, interaction: discord.Interaction):
        # TODO: Port aus deinem alten !setup
        await reply_text(interaction, "🛠️ Setup-Wizard (TODO)")

    @app_commands.command(name="disable", description="Deaktiviert ein Modul oder eine Funktion")
    @require_manage_guild()
    async def disable(self, interaction: discord.Interaction, module: str):
        # TODO: Port aus deinem alten !disable
        await reply_text(interaction, f"📴 {module} wurde deaktiviert (TODO).")

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
