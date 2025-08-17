# bot/utils/timeutil.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import discord
from ..services.translation import translate_de_to_en

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
    """(Placeholder) – falls du Titel/Felder abhängig von Guild-Sprache übersetzen willst."""
    # Beispiel: embed.title = await translate_de_to_en(embed.title) wenn guild.lang == 'en'
    return embed