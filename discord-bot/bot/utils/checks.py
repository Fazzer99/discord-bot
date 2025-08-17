# bot/utils/checks.py
from __future__ import annotations
import discord
from discord import app_commands

# ----------------------------- Rechte-Checks -----------------------------

def require_manage_guild():
    """Slash-Check: Nutzer braucht 'Server verwalten' oder Admin. Wirft MissingPermissions bei Verstoß."""
    def predicate(inter: discord.Interaction) -> bool:
        if inter.guild is None:
            raise app_commands.CheckFailure("Guild-only command")
        perms = getattr(inter.user, "guild_permissions", None)
        if not perms or not (perms.manage_guild or perms.administrator):
            raise app_commands.MissingPermissions(["manage_guild"])
        return True
    return app_commands.check(predicate)

def require_manage_channels():
    """Slash-Check: Nutzer braucht 'Kanäle verwalten' oder Admin. Wirft MissingPermissions bei Verstoß."""
    def predicate(inter: discord.Interaction) -> bool:
        if inter.guild is None:
            raise app_commands.CheckFailure("Guild-only command")
        perms = getattr(inter.user, "guild_permissions", None)
        if not perms or not (perms.manage_channels or perms.administrator):
            raise app_commands.MissingPermissions(["manage_channels"])
        return True
    return app_commands.check(predicate)

def require_manage_messages():
    """Slash-Check: Nutzer braucht 'Nachrichten verwalten' oder Admin. Wirft MissingPermissions bei Verstoß."""
    def predicate(inter: discord.Interaction) -> bool:
        if inter.guild is None:
            raise app_commands.CheckFailure("Guild-only command")
        perms = getattr(inter.user, "guild_permissions", None)
        if not perms or not (perms.manage_messages or perms.administrator):
            raise app_commands.MissingPermissions(["manage_messages"])
        return True
    return app_commands.check(predicate)

# ------------------------ Globaler Onboarding-Guard ----------------------

async def ensure_onboarded(interaction: discord.Interaction) -> bool:
    """
    True -> Guild hat Sprache (de|en) UND Zeitzone (tz=UTC-Minuten) gesetzt.
    Ausnahmefälle: DMs oder Befehle /setlang, /onboard, /set_timezone.
    """
    if interaction.guild is None:
        return True

    cmd_name = interaction.command.name if interaction.command else ""
    if cmd_name in {"setlang", "onboard", "set_timezone"}:
        return True

    from ..services.guild_config import get_guild_cfg
    from .replies import reply_text

    cfg = await get_guild_cfg(interaction.guild.id)
    lang = (cfg.get("lang") or "").lower()
    tz = cfg.get("tz")  # Minuten (int) erwartet

    if lang in ("de", "en") and isinstance(tz, int):
        return True

    # Hinweis + Abbruch
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    await reply_text(
        interaction,
        "🧩 Dieser Server ist noch nicht vollständig eingerichtet.\n"
        "Bitte führe **/onboard** aus und wähle die Sprache **(de|en)** sowie den **UTC-Offset** "
        "(z. B. `+2`, `-5.75`, `+4.5`).",
        kind="warning",
        ephemeral=True,
    )
    raise app_commands.CheckFailure("Guild not onboarded")

class GuildOnboardGuard:
    """
    Mixin für Cogs: Führt vor JEDEM App-Command der Cog den Onboarding-Check aus.
    Anwendung: class MyCog(GuildOnboardGuard, commands.Cog): ...
    """
    async def cog_app_command_check(self, interaction: discord.Interaction) -> bool:  # type: ignore[override]
        return await ensure_onboarded(interaction)