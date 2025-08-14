# bot/utils/checks.py
from __future__ import annotations
import discord
from discord import app_commands

def require_manage_guild():
    """Slash-Check: Nutzer braucht 'Server verwalten' oder Admin."""
    return app_commands.check(
        lambda inter: bool(
            inter.guild
            and inter.user
            and (inter.user.guild_permissions.manage_guild or inter.user.guild_permissions.administrator)
        )
    )

def require_manage_channels():
    """Slash-Check: Nutzer braucht 'Kanäle verwalten' oder Admin."""
    return app_commands.check(
        lambda inter: bool(
            inter.guild
            and inter.user
            and (inter.user.guild_permissions.manage_channels or inter.user.guild_permissions.administrator)
        )
    )

def require_manage_messages():
    """Slash-Check: Nutzer braucht 'Nachrichten verwalten' oder Admin."""
    return app_commands.check(
        lambda inter: bool(
            inter.guild
            and inter.user
            and (inter.user.guild_permissions.manage_messages or inter.user.guild_permissions.administrator)
        )
    )
