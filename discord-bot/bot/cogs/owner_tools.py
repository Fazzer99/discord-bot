# bot/cogs/owner_tools.py
from __future__ import annotations
import json
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands

from ..config import settings
from ..utils.replies import reply_text
from ..services.git_features import commit_features_json  # optionaler Git-Commit

FEATURES_PATH = Path(__file__).resolve().parents[2] / "data" / "features.json"

def _load_features() -> list[tuple[str, str]]:
    if FEATURES_PATH.exists():
        try:
            return [tuple(x) for x in json.loads(FEATURES_PATH.read_text(encoding="utf-8"))]
        except Exception:
            return []
    return []

def _save_features(features: list[tuple[str, str]]) -> None:
    FEATURES_PATH.write_text(json.dumps(features, ensure_ascii=False, indent=2), encoding="utf-8")


class OwnerToolsCog(commands.Cog):
    """Sammel-Cog für Owner-only Werkzeuge (Serverliste, Feature-Pflege, …)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ----------------------------- Owner-Guard -----------------------------
    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != settings.owner_id:
            await reply_text(interaction, "❌ Nur der Bot-Owner darf diesen Befehl nutzen.", kind="error", ephemeral=True)
            return False
        return True

    # ----------------------------- /bot_guilds -----------------------------
    @app_commands.command(name="bot_guilds", description="(Owner) Liste aller Server: Name + ID.")
    @app_commands.describe(query="Optional: Filter (Teil vom Servernamen)")
    async def bot_guilds(self, interaction: discord.Interaction, query: str | None = None):
        if not await self._ensure_owner(interaction):
            return

        guilds = list(self.bot.guilds)
        if query:
            q = query.lower()
            guilds = [g for g in guilds if (g.name or "").lower().find(q) != -1]

        guilds.sort(key=lambda g: (g.name or "").lower())
        lines = [f"• **{g.name}** — `{g.id}`" for g in guilds]

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # In Embeds paginieren (Discord-Limits)
        pages: list[list[str]] = []
        cur: list[str] = []
        cur_len = 0
        for line in lines:
            if cur_len + len(line) + 1 > 3900 or len(cur) >= 60:  # etwas Puffer
                pages.append(cur)
                cur, cur_len = [], 0
            cur.append(line)
            cur_len += len(line) + 1
        if cur:
            pages.append(cur)

        if not pages:
            return await reply_text(interaction, "ℹ️ Der Bot ist aktuell in **keinem** Server.", ephemeral=True)

        title = f"🤖 Bot-Server ({len(guilds)})"
        emb = discord.Embed(title=title, description="\n".join(pages[0]), color=discord.Color.blurple())
        await interaction.followup.send(embed=emb, ephemeral=True)

        for i in range(1, len(pages)):
            emb = discord.Embed(title=title + f" – Seite {i+1}", description="\n".join(pages[i]), color=discord.Color.blurple())
            await interaction.followup.send(embed=emb, ephemeral=True)

    # ----------------------------- /add_feature ----------------------------
    @app_commands.command(name="add_feature", description="(Owner) Feature zur Liste hinzufügen")
    @app_commands.describe(name="Feature-Name", description="Beschreibung (Markdown erlaubt)")
    async def add_feature(self, interaction: discord.Interaction, name: str, description: str):
        if not await self._ensure_owner(interaction):
            return

        features = _load_features()
        if any(n.lower() == name.lower() for n, _ in features):
            return await reply_text(interaction, f"⚠️ Feature `{name}` existiert bereits.", ephemeral=True)

        features.append((name, description))
        _save_features(features)

        # Optional: GitHub Commit (best effort)
        ok = await commit_features_json(features)
        note = " (Git commit ✓)" if ok else ""
        await reply_text(interaction, f"✅ Feature `{name}` hinzugefügt{note}.", kind="success", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerToolsCog(bot))