# bot/utils/checks.py
from __future__ import annotations
import functools
import discord
from discord import app_commands

# ---- App-Command Checks (Slash) -------------------------------------------

def require_manage_guild():
    """Slash-Check: Nutzer braucht 'Server verwalten'."""
    def predicate(inter: discord.Interaction) -> bool:
        if inter.user is None or inter.guild is None:
            return False
        perms = inter.user.guild_permissions
        return bool(perms.manage_guild or perms.administrator)
    return app_commands.check(lambda inter: predicate(inter))

def require_manage_channels():
    """Slash-Check: Nutzer braucht 'Kanäle verwalten'."""
    def predicate(inter: discord.Interaction) -> bool:
        if inter.user is None or inter.guild is None:
            return False
        perms = inter.user.guild_permissions
        return bool(perms.manage_channels or perms.administrator)
    return app_commands.check(lambda inter: predicate(inter))

def require_manage_messages():
    """Slash-Check: Nutzer braucht 'Nachrichten verwalten'."""
    def predicate(inter: discord.Interaction) -> bool:
        if inter.user is None or inter.guild is None:
            return False
        perms = inter.user.guild_permissions
        return bool(perms.manage_messages or perms.administrator)
    return app_commands.check(lambda inter: predicate(inter))