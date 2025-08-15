# bot/utils/checks.py
from __future__ import annotations
import discord
from discord import app_commands

# ----------------------------- Rechte-Checks -----------------------------

def require_manage_guild():
    """Slash-Check: Nutzer braucht 'Server verwalten' oder Admin."""
    return app_commands.check(
        lambda inter: bool(
            inter.guild
            and inter.user
            and (
                inter.user.guild_permissions.manage_guild
                or inter.user.guild_permissions.administrator
            )
        )
    )

def require_manage_channels():
    """Slash-Check: Nutzer braucht 'Kanäle verwalten' oder Admin."""
    return app_commands.check(
        lambda inter: bool(
            inter.guild
            and inter.user
            and (
                inter.user.guild_permissions.manage_channels
                or inter.user.guild_permissions.administrator
            )
        )
    )

def require_manage_messages():
    """Slash-Check: Nutzer braucht 'Nachrichten verwalten' oder Admin."""
    return app_commands.check(
        lambda inter: bool(
            inter.guild
            and inter.user
            and (
                inter.user.guild_permissions.manage_messages
                or inter.user.guild_permissions.administrator
            )
        )
    )

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