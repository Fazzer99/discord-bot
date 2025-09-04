# bot/cogs/features.py
from __future__ import annotations
import json
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from ..utils.replies import reply_text

FEATURES_PATH = Path(__file__).resolve().parents[2] / "data" / "features.json"

def load_features() -> list[tuple[str, str]]:
    if FEATURES_PATH.exists():
        try:
            return [tuple(x) for x in json.loads(FEATURES_PATH.read_text(encoding="utf-8"))]
        except Exception:
            return []
    return []

class FeaturesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="features", description="Zeige die aktuelle Feature-Liste")
    async def features(self, interaction: discord.Interaction):
        features = load_features()
        if not features:
            return await reply_text(interaction, "Keine Features eingetragen.", ephemeral=True)

        embeds: list[discord.Embed] = []
        current = discord.Embed(title="📋 Aktuelle Features", color=discord.Color.blurple())
        total_chars = 0

        for name, desc in features:
            value = desc.replace("\n", "\n")
            if len(value) > 1024:
                parts = [value[i:i+1024] for i in range(0, len(value), 1024)]
                current.add_field(name=name, value=parts[0], inline=False)
                for p in parts[1:]:
                    current.add_field(name="↳ Fortsetzung", value=p, inline=False)
            else:
                current.add_field(name=name, value=value, inline=False)

            total_chars += len(name) + len(value)
            if len(current.fields) >= 25 or total_chars > 5500:
                embeds.append(current)
                current = discord.Embed(color=discord.Color.blurple())
                total_chars = 0

        if len(current.fields) > 0:
            embeds.append(current)

        # Erste Antwort via interaction.response, Rest via followup
        await interaction.response.send_message(embed=embeds[0])
        for e in embeds[1:]:
            await interaction.followup.send(embed=e)

async def setup(bot: commands.Bot):
    await bot.add_cog(FeaturesCog(bot))