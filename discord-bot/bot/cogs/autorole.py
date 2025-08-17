# bot/cogs/autorole.py
from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands

from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..utils.replies import reply_success, reply_error, reply_text
from ..utils.checks import require_manage_guild

class AutoroleCog(commands.Cog):
    """
    Autorole:
      - on_member_join: weist automatisch die in guild_settings.default_role gespeicherte Rolle zu (ohne Chat-Ausgabe)
      - /set_autorole <role>: setzt die Auto-Rolle
      - /clear_autorole: deaktiviert Autorole
      - /autorole: zeigt die aktuelle Einstellung
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- Event: neuen Mitgliedern automatisch die konfigurierte Rolle geben (silent) ---
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        cfg = await get_guild_cfg(member.guild.id)
        role_id = cfg.get("default_role")
        if not role_id:
            return  # keine Autorole konfiguriert

        role = member.guild.get_role(role_id)
        if not role:
            return  # Rolle existiert nicht mehr

        try:
            await member.add_roles(role, reason="Autorole Setup")
        except discord.Forbidden:
            # Keine Channel-Nachricht mehr – nur Log, damit dein Welcome-Feature allein spricht
            print(f"[Autorole] ❗️ Keine Berechtigung für Rolle {role_id} in Guild {member.guild.id}")

    # --- Slash: /set_autorole -------------------------------------------------
    @app_commands.command(
        name="set_autorole",
        description="Setzt die Rolle, die neuen Mitgliedern automatisch zugewiesen wird."
    )
    @require_manage_guild()
    @app_commands.describe(
        role="Rolle, die neuen Mitgliedern automatisch zugewiesen werden soll"
    )
    async def set_autorole(self, interaction: discord.Interaction, role: discord.Role):

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        me = guild.me

        if role == guild.default_role:
            return await reply_error(
                interaction,
                "❌ Die @everyone-Rolle kann nicht als Autorole gesetzt werden."
            )

        if me.top_role <= role:
            return await reply_error(
                interaction,
                "❌ Ich kann diese Rolle nicht zuweisen, weil sie gleich hoch oder höher als meine höchste Rolle ist.\n"
                "Bitte ziehe meine Bot-Rolle in der Rollenliste über die gewünschte Autorole."
            )

        if not guild.me.guild_permissions.manage_roles:
            return await reply_error(
                interaction,
                "❌ Mir fehlt die Berechtigung **Rollen verwalten** (Manage Roles)."
            )

        await update_guild_cfg(guild.id, default_role=role.id)
        return await reply_success(
            interaction,
            f"✅ Autorole gesetzt auf {role.mention}."
        )

    # --- Slash: /clear_autorole ----------------------------------------------
    @app_commands.command(
        name="clear_autorole",
        description="Deaktiviert die automatische Rollenvergabe für neue Mitglieder."
    )
    @require_manage_guild()
    async def clear_autorole(self, interaction: discord.Interaction):
        await update_guild_cfg(interaction.guild.id, default_role=None)
        return await reply_success(
            interaction,
            "🗑️ Autorole wurde deaktiviert. Es wird keine Rolle mehr automatisch vergeben."
        )

    # --- Slash: /autorole (Status anzeigen) ----------------------------------
    @app_commands.command(
        name="autorole",
        description="Zeigt die aktuell konfigurierte Auto-Rolle (falls vorhanden)."
    )
    async def autorole_status(self, interaction: discord.Interaction):
        cfg = await get_guild_cfg(interaction.guild.id)
        role_id = cfg.get("default_role")
        if not role_id:
            return await reply_text(
                interaction,
                "ℹ️ Es ist derzeit **keine Autorole** konfiguriert.",
                kind="info"
            )

        role = interaction.guild.get_role(role_id)
        if role is None:
            return await reply_text(
                interaction,
                f"⚠️ Es ist eine Autorole mit der ID `{role_id}` gespeichert, die Rolle existiert aber nicht mehr.",
                kind="warning"
            )

        return await reply_text(
            interaction,
            f"🔧 Aktuelle Autorole: {role.mention}",
            kind="info"
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(AutoroleCog(bot))