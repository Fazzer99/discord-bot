# bot/utils/timeutil.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import discord
from ..services.translation import translate_text_for_guild

# ----------------------------- TZ-Utilities -----------------------------

def get_tz_delta(minutes: int | str | None) -> timedelta:
    try:
        return timedelta(minutes=int(str(minutes).strip()))
    except Exception:
        return timedelta(0)

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def to_local(dt_utc: datetime, tz_minutes: int | str | None) -> datetime:
    """UTC -> lokale naive Zeit (kein tzinfo)."""
    return (dt_utc + get_tz_delta(tz_minutes)).replace(tzinfo=None)

def local_to_utc(dt_local_naive: datetime, tz_minutes: int | str | None) -> datetime:
    """Lokale naive Zeit -> UTC-aware."""
    return (dt_local_naive - get_tz_delta(tz_minutes)).replace(tzinfo=timezone.utc)

# ----------------------------- Embed-Übersetzer -----------------------------

async def translate_embed(guild_id: int, embed: discord.Embed) -> discord.Embed:
    """
    Übersetzt Titel, Beschreibung und Felder eines Embeds gemäß Guild-Sprache.
    Nutzt translate_text_for_guild, d. h. DE bleibt DE, EN-Server bekommen EN.
    """
    # Neue Embed-Kopie bauen, um das Original nicht zu mutieren
    translated = discord.Embed(
        title=embed.title or None,
        description=embed.description or None,
        color=embed.color
    )

    # Author / Footer übernehmen
    if embed.author:
        translated.set_author(
            name=getattr(embed.author, "name", None) or discord.Embed.Empty,
            icon_url=getattr(embed.author, "icon_url", None) or discord.Embed.Empty
        )
    if embed.footer and embed.footer.text:
        translated.set_footer(text=embed.footer.text)

    # Titel/Beschreibung übersetzen
    if translated.title:
        translated.title = await translate_text_for_guild(guild_id, translated.title)
    if translated.description:
        translated.description = await translate_text_for_guild(guild_id, translated.description)

    # Felder übersetzen
    for f in embed.fields:
        name = await translate_text_for_guild(guild_id, f.name) if f.name else ""
        value = await translate_text_for_guild(guild_id, f.value) if f.value else ""
        translated.add_field(name=name, value=value, inline=f.inline)

    # Thumbnails / Images / URLs übernehmen
    if embed.thumbnail and embed.thumbnail.url:
        translated.set_thumbnail(url=embed.thumbnail.url)
    if embed.image and embed.image.url:
        translated.set_image(url=embed.image.url)
    if embed.url:
        translated.url = embed.url

    return translated