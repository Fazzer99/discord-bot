# bot/utils/checks.py
from __future__ import annotations
import discord
from discord import app_commands

# ----------------------------- Rechte-Checks -----------------------------

def require_manage_guild():
    """Slash-Check: Nutzer braucht 'Server verwalten' oder Admin. Wirft MissingPermissions bei Verstoß."""
    def predicate(inter: discord.Interaction) -> bool:
        if inter.guild is None:
            # In DMs macht der Befehl keinen Sinn
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

# ------------------------ Globaler Sprach-Guard --------------------------

async def ensure_lang_for_interaction(interaction: discord.Interaction) -> bool:
    """
    True -> Sprache ist gesetzt (de|en) oder Ausnahmefall.
    Ausnahmefälle: DMs (keine Guild) oder der Befehl ist 'setlang'.
    Wenn Sprache fehlt, wird ein Hinweis-Embed gesendet und ein CheckFailure geworfen.
    """
    # DMs / keine Guild: nicht blocken
    if interaction.guild is None:
        return True

    # /setlang darf immer durch
    cmd_name = interaction.command.name if interaction.command else ""
    if cmd_name == "setlang":
        return True

    # Lazy-Imports vermeiden Zirkularimporte
    from ..services.guild_config import get_guild_cfg
    from .replies import reply_text

    cfg = await get_guild_cfg(interaction.guild.id)
    lang = (cfg.get("lang") or "").lower()
    if lang in ("de", "en"):
        return True

    # Sprache nicht gesetzt -> Hinweis + Abbruch
    await reply_text(
        interaction,
        "🌐 Bitte zuerst die Sprache wählen mit `/setlang de` oder `/setlang en`.\n"
        "🌐 Please choose a language first: `/setlang de` or `/setlang en`.",
        kind="warning",
        ephemeral=True,
    )
    raise app_commands.CheckFailure("Guild language not set")

class GuildLangGuard:
    """
    Mixin für Cogs: Führt vor JEDEM App-Command der Cog den Sprach-Check aus.
    Anwendung: class MyCog(GuildLangGuard, commands.Cog): ...
    """
    async def cog_app_command_check(self, interaction: discord.Interaction) -> bool:  # type: ignore[override]
        return await ensure_lang_for_interaction(interaction)