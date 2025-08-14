import discord
from .timeutil import translate_embed
from ..services.translation import translate_de_to_en
from ..db import fetchrow
from typing import Optional

async def get_guild_cfg(guild_id: int) -> dict:
    row = await fetchrow("SELECT * FROM guild_settings WHERE guild_id = $1", guild_id)
    return dict(row) if row else {}

async def reply_text(ctx_or_inter, text_de: str, **fmt):
    """Sendet Text in Gilde-Sprache (de/en). Works for ctx or interaction."""
    text_de = text_de.format(**fmt) if fmt else text_de
    guild_id = (getattr(ctx_or_inter, "guild", None) or getattr(getattr(ctx_or_inter, "channel", None), "guild", None)).id
    lang = (await get_guild_cfg(guild_id)).get("lang", "de") if guild_id else "de"
    if lang.lower() == "en":
        text = await translate_de_to_en(text_de)
    else:
        text = text_de
    if hasattr(ctx_or_inter, "response"):
        inter = ctx_or_inter
        if not inter.response.is_done():
            return await inter.response.send_message(text)
        else:
            return await inter.followup.send(text)
    else:
        return await ctx_or_inter.send(text)

async def send_embed(ctx_or_inter, embed: discord.Embed):
    guild_id = (getattr(ctx_or_inter, "guild", None) or getattr(getattr(ctx_or_inter, "channel", None), "guild", None)).id
    embed = await translate_embed(guild_id, embed)
    if hasattr(ctx_or_inter, "response"):
        inter = ctx_or_inter
        if not inter.response.is_done():
            return await inter.response.send_message(embed=embed)
        else:
            return await inter.followup.send(embed=embed)
    else:
        return await ctx_or_inter.send(embed=embed)
