import discord
from ..services.translation import translate_de_to_en

async def translate_embed(guild_id: int, embed: discord.Embed) -> discord.Embed:
    """Translate embed fields if guild uses EN. (Placeholder – customize as needed.)"""
    # In a real migration, fetch lang from DB and translate title/fields like in your old code.
    return embed
