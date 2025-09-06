# bot/usage.py
from __future__ import annotations
from typing import Optional, Iterable
from datetime import datetime, timezone

import discord
from discord.ext import commands

from .db import execute
from .services.guild_config import get_guild_cfg


# ---------- kleine Helfer ----------------------------------------------------

def _safe_len(s: Optional[str]) -> int:
    return len(s or "")

def count_embed_chars(embed: discord.Embed) -> int:
    """Zählt alle sichtbaren Texte im Embed."""
    n = 0
    n += _safe_len(embed.title)
    n += _safe_len(embed.description)
    # Footer
    try:
        if embed.footer and getattr(embed.footer, "text", None):
            n += _safe_len(embed.footer.text)
    except Exception:
        pass
    # Author
    try:
        if embed.author and getattr(embed.author, "name", None):
            n += _safe_len(embed.author.name)
    except Exception:
        pass
    # Fields
    for f in getattr(embed, "fields", []) or []:
        n += _safe_len(getattr(f, "name", None))
        n += _safe_len(getattr(f, "value", None))
    return n

def total_message_chars(content: Optional[str], embeds: Iterable[discord.Embed] | None) -> int:
    total = _safe_len(content)
    if embeds:
        for e in embeds:
            total += count_embed_chars(e)
    return total

async def _guild_lang(guild_id: Optional[int]) -> str:
    """
    Liest die Guild-Sprache aus der Config; Fallback:
      - 'dm' bei DMs (kein Guild-Kontext)
      - sonst 'de'
    """
    if not guild_id:
        return "dm"
    try:
        cfg = await get_guild_cfg(guild_id)
        lang = str(cfg.get("lang") or "de").lower()
        return lang if lang in {"de", "en"} else "de"
    except Exception:
        return "de"


# ---------- Ephemeral-Logger (für replies.py) --------------------------------

async def log_interaction_output(
    inter: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    embeds: Optional[list[discord.Embed]] = None,
    message_type: str = "ephemeral",  # rein informativ
) -> None:
    """
    Loggt eine Interaction-Antwort – typischerweise 'ephemeral', da diese
    kein on_message-Event auslösen. Aus replies.py nach erfolgreichem Senden aufrufen.
    """
    try:
        if embeds is None and embed is not None:
            embeds = [embed]
        chars = total_message_chars(content, embeds or [])
        if chars <= 0:
            return

        guild_id = inter.guild_id
        channel_id = inter.channel_id
        user_id = inter.user.id if inter.user else None
        lang = await _guild_lang(guild_id)

        await execute(
            """
            INSERT INTO public.output_usage
                (ts, guild_id, channel_id, user_id, message_type, chars, lang, is_dm, is_ephemeral)
            VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8)
            """,
            guild_id,
            channel_id,
            user_id,
            message_type,
            int(chars),
            lang,
            False,           # Interactions sind keine DMs
            True,            # hier speziell ephemeral
        )
    except Exception:
        # Logging darf nie Antworten blockieren
        pass


# ---------- Cog: sichtbare Nachrichten des Bots (Channel & DM) ----------------

class UsageLogger(commands.Cog):
    """Loggt alle vom Bot gesendeten, sichtbaren Nachrichten (Channel & DM; nicht ephemeral)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        # Nur Nachrichten des *eigenen* Bots
        if not msg.author.bot or not self.bot.user or msg.author.id != self.bot.user.id:
            return

        # Ephemeral erscheinen hier nie; alles hier ist sichtbar
        try:
            # GroupChannel ist für Bot-Accounts praktisch irrelevant, aber der Check ist harmlos
            GroupChannel = getattr(discord, "GroupChannel", None)
            if GroupChannel:
                is_dm = isinstance(msg.channel, (discord.DMChannel, GroupChannel))
            else:
                is_dm = isinstance(msg.channel, discord.DMChannel)

            guild_id = msg.guild.id if msg.guild else None
            channel_id = msg.channel.id

            # Bei DM: Empfänger (best effort)
            user_id = None
            if is_dm:
                try:
                    user_id = getattr(getattr(msg, "channel", None), "recipient", None)
                    user_id = getattr(user_id, "id", None)
                except Exception:
                    user_id = None

            chars = total_message_chars(msg.content, msg.embeds)
            if chars <= 0:
                return

            lang = await _guild_lang(guild_id)

            await execute(
                """
                INSERT INTO public.output_usage
                    (ts, guild_id, channel_id, user_id, message_type, chars, lang, is_dm, is_ephemeral)
                VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8)
                """,
                guild_id,
                channel_id,
                user_id,
                "dm" if is_dm else "channel",
                int(chars),
                lang,
                bool(is_dm),
                False,
            )
        except Exception:
            # Logging darf nie die Laufzeit stören
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(UsageLogger(bot))