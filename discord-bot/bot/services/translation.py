# bot/services/translation.py
from __future__ import annotations
import os
import aiohttp
import asyncio
import discord
from typing import Optional, Dict

from ..services.guild_config import get_guild_cfg

DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_KEY = os.getenv("DEEPL_API_KEY")

# Cache: deutscher Text -> englischer Text
_translation_cache: Dict[str, str] = {}

async def translate_de_to_en(text_de: str) -> str:
    """Übersetzt DE->EN mit Cache & Timeouts. Fällt bei Fehlern auf Original zurück."""
    if not text_de or not text_de.strip():
        return text_de
    if text_de in _translation_cache:
        return _translation_cache[text_de]
    if not DEEPL_KEY:
        return text_de

    payload = {
        "auth_key": DEEPL_KEY,
        "text": text_de,
        "source_lang": "DE",
        "target_lang": "EN",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(DEEPL_API_URL, data=payload) as resp:
                if resp.status != 200:
                    return text_de
                data = await resp.json()
                en = data["translations"][0]["text"]
                _translation_cache[text_de] = en
                return en
    except asyncio.TimeoutError:
        return text_de
    except Exception:
        return text_de

async def translate_text_for_guild(guild_id: Optional[int], text_de: str) -> str:
    """
    Gibt den Text ggf. auf Englisch zurück, wenn guild.lang == 'en'.
    """
    if not text_de or guild_id is None:
        return text_de
    try:
        cfg = await get_guild_cfg(guild_id)
    except Exception:
        return text_de
    lang = (cfg.get("lang") or "").lower()
    if lang == "en":
        return await translate_de_to_en(text_de)
    return text_de

async def translate_embed_for_guild(guild_id: int, embed: discord.Embed) -> discord.Embed:
    """
    Übersetzt Embed-Inhalte DE->EN, wenn guild.lang == 'en'.
    """
    if embed is None:
        return embed
    try:
        cfg = await get_guild_cfg(guild_id)
    except Exception:
        return embed
    if (cfg.get("lang") or "").lower() != "en":
        return embed

    # Titel & Beschreibung
    if embed.title:
        embed.title = await translate_de_to_en(embed.title)
    if embed.description:
        embed.description = await translate_de_to_en(embed.description)

    # Felder
    if embed.fields:
        old = list(embed.fields)
        embed.clear_fields()
        for f in old:
            name = await translate_de_to_en(f.name) if f.name else f.name
            value = await translate_de_to_en(f.value) if f.value else f.value
            embed.add_field(name=name, value=value, inline=f.inline)

    # Footer
    if embed.footer and embed.footer.text:
        embed.set_footer(text=await translate_de_to_en(embed.footer.text), icon_url=embed.footer.icon_url)

    # Author
    if embed.author and embed.author.name:
        embed.set_author(name=await translate_de_to_en(embed.author.name),
                         url=embed.author.url,
                         icon_url=embed.author.icon_url)
    return embed