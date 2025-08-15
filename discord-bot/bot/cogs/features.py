from __future__ import annotations
import json
import discord
from discord import app_commands
from discord.ext import commands
from pathlib import Path
from ..utils.replies import send_embed, reply_text
from ..services.git_features import commit_features_json
from ..config import settings
from ..utils.checks import GuildLangGuard

FEATURES_PATH = Path(__file__).resolve().parents[2] / "data" / "features.json"

def load_features() -> list[tuple[str, str]]:
    if FEATURES_PATH.exists():
        try:
            return [tuple(x) for x in json.loads(FEATURES_PATH.read_text(encoding="utf-8"))]
        except Exception:
            return []
    return []

def save_features(features: list[tuple[str, str]]):
    FEATURES_PATH.write_text(json.dumps(features, ensure_ascii=False, indent=2), encoding="utf-8")

class FeaturesCog(GuildLangGuard, commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="features", description="Zeige die aktuelle Feature-Liste")
    async def features(self, interaction: discord.Interaction):
        features = load_features()
        if not features:
            return await reply_text(interaction, "Keine Features eingetragen.")

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

        # First response must be via interaction response; subsequent via followup
        await interaction.response.send_message(embed=embeds[0])
        for e in embeds[1:]:
            await interaction.followup.send(embed=e)

    @app_commands.command(name="add_feature", description="Feature zur Liste hinzufügen (Owner only)")
    async def add_feature(self, interaction: discord.Interaction, name: str, description: str):
        if interaction.user.id != settings.owner_id:
            return await reply_text(interaction, "❌ Du darfst diesen Befehl nicht nutzen.")

        features = load_features()
        if any(n.lower() == name.lower() for n, _ in features):
            return await reply_text(interaction, f"⚠️ Feature `{name}` existiert bereits.")

        features.append((name, description))
        save_features(features)

        # Optional GitHub commit (best-effort)
        ok = await commit_features_json(features)
        note = " (mit GitHub-Commit)" if ok else ""
        await reply_text(interaction, f"✅ Feature `{name}` hinzugefügt{note}.")

async def setup(bot: commands.Bot):
    await bot.add_cog(FeaturesCog(bot))
