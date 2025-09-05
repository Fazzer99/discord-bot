# bot/cogs/owner_tools.py
from __future__ import annotations
import json
import asyncio  # ← NEU
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands

from ..config import settings
from ..utils.replies import reply_text
from ..services.git_features import commit_features_json  # optionaler Git-Commit
from ..db import fetch, fetchrow, execute  # DB-Helfer für Bans

FEATURES_PATH = Path(__file__).resolve().parents[2] / "data" / "features.json"

# ---- NEU: Top.gg Vote-Link + In-Memory Task-Registry ----
TOPGG_VOTE_URL = "https://top.gg/bot/1387561449592848454/vote"
VOTE_BROADCAST_TASKS: dict[int, asyncio.Task] = {}  # channel_id -> task


def _load_features() -> list[tuple[str, str]]:
    if FEATURES_PATH.exists():
        try:
            return [tuple(x) for x in json.loads(FEATURES_PATH.read_text(encoding="utf-8"))]
        except Exception:
            return []
    return []


def _save_features(features: list[tuple[str, str]]) -> None:
    FEATURES_PATH.write_text(json.dumps(features, ensure_ascii=False, indent=2), encoding="utf-8")


# ---- NEU: Einfache View mit Vote-Link-Button (ohne Timeout) ----
class VoteSimpleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Jetzt für Ignix voten ❤️",
                style=discord.ButtonStyle.link,
                url=TOPGG_VOTE_URL,
            )
        )


class OwnerToolsCog(commands.Cog):
    """Owner-only Werkzeuge (Serverliste, Feature-Pflege, Bot verlassen lassen, permanente Bot-Bans)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != settings.owner_id:
            await reply_text(
                interaction,
                "❌ Nur der Bot-Owner darf diesen Befehl nutzen.",
                kind="error",
                ephemeral=True,
            )
            return False
        return True

    # ───────────────────────── /bot_guilds ─────────────────────────
    @app_commands.command(name="bot_guilds", description="(Owner) Liste aller Server: Name + ID.")
    @app_commands.describe(query="Optional: Filter (Teil vom Servernamen)")
    async def list_bot_guilds(self, interaction: discord.Interaction, query: str | None = None):
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

        # In Embeds paginieren
        pages: list[list[str]] = []
        cur: list[str] = []
        cur_len = 0
        for line in lines:
            if cur_len + len(line) + 1 > 3900 or len(cur) >= 60:
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
            emb = discord.Embed(
                title=title + f" – Seite {i+1}",
                description="\n".join(pages[i]),
                color=discord.Color.blurple(),
            )
            await interaction.followup.send(embed=emb, ephemeral=True)

    # ───────────────────────── /add_feature ────────────────────────
    @app_commands.command(name="add_feature", description="(Owner) Feature zur Liste hinzufügen")
    @app_commands.describe(name="Feature-Name", description="Beschreibung (Markdown erlaubt)")
    async def add_feature(self, interaction: discord.Interaction, name: str, description: str):
        if not await self._ensure_owner(interaction):
            return

        features = _load_features()
        if any(n.lower() == name.lower() for n, _ in features):
            return await reply_text(
                interaction,
                f"⚠️ Feature `{name}` existiert bereits.",
                ephemeral=True,
            )

        features.append((name, description))
        _save_features(features)

        ok = await commit_features_json(features)  # best-effort
        note = " (Git commit ✓)" if ok else ""
        await reply_text(
            interaction,
            f"✅ Feature `{name}` hinzugefügt{note}.",
            kind="success",
            ephemeral=True,
        )

    # ───────────────────────── /bot_leave ──────────────────────────
    @app_commands.command(
        name="bot_leave",
        description="(Owner) Lässt den Bot einen Server verlassen (per Guild-ID)."
    )
    @app_commands.describe(
        guild_id="Die Guild-ID des Servers",
        reason="Optionaler Grund (nur als Notiz)"
    )
    async def leave_guild(self, interaction: discord.Interaction, guild_id: str, reason: str | None = None):
        if not await self._ensure_owner(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        try:
            gid = int(guild_id)
        except ValueError:
            return await reply_text(
                interaction,
                "❌ Ungültige Guild-ID (keine Zahl).",
                kind="error",
                ephemeral=True,
            )

        guild = self.bot.get_guild(gid)
        if guild is None:
            return await reply_text(
                interaction,
                f"ℹ️ Der Bot ist aktuell **nicht** in einer Guild mit ID `{gid}`.",
                ephemeral=True,
            )

        name = guild.name or "Unbekannt"
        try:
            await guild.leave()
        except discord.Forbidden:
            return await reply_text(
                interaction,
                "❌ Keine Berechtigung, diese Guild zu verlassen.",
                kind="error",
                ephemeral=True,
            )
        except Exception as e:
            return await reply_text(
                interaction,
                f"❌ Unerwarteter Fehler beim Verlassen von **{name}** (`{gid}`): {e}",
                kind="error",
                ephemeral=True,
            )

        msg = f"✅ Bot hat **{name}** (`{gid}`) verlassen."
        if reason:
            msg += f"\nNotiz: {reason}"
        await reply_text(interaction, msg, kind="success", ephemeral=True)

    # ───────────────────────── Permanente Bot-Bans ─────────────────────────
    @app_commands.command(name="bot_ban", description="(Owner) Bannt eine Guild dauerhaft (Bot kann nicht mehr hinzugefügt werden).")
    @app_commands.describe(guild_id="Guild-ID", reason="Optionaler Grund")
    async def ban_guild(self, interaction: discord.Interaction, guild_id: str, reason: str | None = None):
        if not await self._ensure_owner(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        try:
            gid = int(guild_id)
        except ValueError:
            return await reply_text(interaction, "❌ Ungültige Guild-ID (keine Zahl).", kind="error", ephemeral=True)

        # Bereits gebannt?
        existing = await fetchrow("SELECT guild_id FROM public.bot_bans WHERE guild_id=$1", gid)
        if existing:
            # Nur Grund (reason) aktualisieren, falls angegeben
            if reason:
                await execute("UPDATE public.bot_bans SET reason=$2 WHERE guild_id=$1", gid, reason)
                return await reply_text(interaction, f"✅ Guild `{gid}` war bereits gebannt – Grund aktualisiert.", kind="success", ephemeral=True)
            return await reply_text(interaction, f"ℹ️ Guild `{gid}` ist bereits gebannt.", ephemeral=True)

        # Neu eintragen
        await execute(
            "INSERT INTO public.bot_bans (guild_id, reason) VALUES ($1, $2)",
            gid, (reason or None)
        )

        # Falls der Bot aktuell drin ist: sofort verlassen
        g = self.bot.get_guild(gid)
        if g:
            try:
                await g.leave()
            except Exception:
                pass

        return await reply_text(
            interaction,
            f"✅ Guild `{gid}` dauerhaft gebannt.{f' Grund: {reason}' if reason else ''}",
            kind="success",
            ephemeral=True,
        )

    @app_commands.command(name="bot_unban", description="(Owner) Entfernt den permanenten Ban einer Guild.")
    @app_commands.describe(guild_id="Guild-ID")
    async def unban_guild(self, interaction: discord.Interaction, guild_id: str):
        if not await self._ensure_owner(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        try:
            gid = int(guild_id)
        except ValueError:
            return await reply_text(interaction, "❌ Ungültige Guild-ID (keine Zahl).", kind="error", ephemeral=True)

        await execute("DELETE FROM public.bot_bans WHERE guild_id=$1", gid)
        return await reply_text(interaction, f"✅ Guild `{gid}` ist nicht länger gebannt.", kind="success", ephemeral=True)

    @app_commands.command(name="bot_bans", description="(Owner) Zeigt die Liste permanent gebannter Guilds.")
    async def list_bans(self, interaction: discord.Interaction):
        if not await self._ensure_owner(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        rows = await fetch(
            "SELECT guild_id, reason, added_at FROM public.bot_bans ORDER BY added_at DESC LIMIT 200"
        )
        if not rows:
            return await reply_text(interaction, "ℹ️ Es sind aktuell **keine** Guilds gebannt.", ephemeral=True)

        lines: list[str] = []
        for r in rows:
            gid = r.get("guild_id")
            reason = r.get("reason") or "—"
            added = r.get("added_at")
            g = self.bot.get_guild(int(gid)) if gid is not None else None
            name = g.name if g else "?"
            if added:
                lines.append(f"• **{name}** — `{gid}` • Grund: {reason} • seit: {added}")
            else:
                lines.append(f"• **{name}** — `{gid}` • Grund: {reason}")

        # Chunken, um Embed-Limits einzuhalten
        pages: list[list[str]] = []
        cur: list[str] = []
        cur_len = 0
        for line in lines:
            if cur_len + len(line) + 1 > 3900 or len(cur) >= 60:
                pages.append(cur)
                cur, cur_len = [], 0
            cur.append(line)
            cur_len += len(line) + 1
        if cur:
            pages.append(cur)

        title = f"🚫 Gebannte Guilds ({len(rows)})"
        emb = discord.Embed(title=title, description="\n".join(pages[0]), color=discord.Color.red())
        await interaction.followup.send(embed=emb, ephemeral=True)
        for i in range(1, len(pages)):
            emb = discord.Embed(
                title=title + f" – Seite {i+1}",
                description="\n".join(pages[i]),
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=emb, ephemeral=True)

    # ──────────────────────── NEU: einfacher Vote-Broadcast ────────────────────────
    @app_commands.command(
        name="vote_broadcast_start",
        description="(Owner) Startet eine wiederkehrende Vote-Erinnerung in einem Kanal (ohne DB)."
    )
    @app_commands.describe(
        guild_id="Guild-ID",
        channel="Kanal, in dem gepostet werden soll",
        every_hours="Intervall in Stunden (min. 1h; Standard 24h)",
        ping_everyone="Ob @everyone enthalten sein soll (Standard: True)"
    )
    async def vote_broadcast_start(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        channel: discord.TextChannel,
        every_hours: int = 24,
        ping_everyone: bool = True,
    ):
        if not await self._ensure_owner(interaction):
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # Guild-ID prüfen
        try:
            gid = int(guild_id)
        except ValueError:
            return await reply_text(interaction, "❌ Ungültige Guild-ID.", kind="error", ephemeral=True)

        if channel.guild.id != gid:
            return await reply_text(interaction, "❌ Der Kanal gehört nicht zu dieser Guild.", kind="error", ephemeral=True)

        # Intervall prüfen
        every_hours = max(1, int(every_hours))
        interval_seconds = every_hours * 3600

        # Falls für diesen Kanal schon läuft → zuerst stoppen
        old = VOTE_BROADCAST_TASKS.pop(channel.id, None)
        if old:
            try:
                old.cancel()
            except Exception:
                pass

        base_msg = (
            "🚀 **Bitte unterstützt Ignix!**\n\n"
            "Wenn euch der Bot gefällt, stimmt bitte für uns auf Top.gg ab. "
            "Das hilft enorm, bekannter zu werden. Vielen Dank! 🙏"
        )
        if ping_everyone:
            base_msg = "@everyone " + base_msg

        view = VoteSimpleView()

        async def _loop():
            while True:
                try:
                    await channel.send(content=base_msg, view=view)
                except discord.Forbidden:
                    break  # keine Rechte -> Task beenden
                except Exception:
                    pass  # unerwartet -> trotzdem weiter im Intervall
                await asyncio.sleep(interval_seconds)

        task = asyncio.create_task(_loop(), name=f"vote_broadcast_{channel.id}")
        VOTE_BROADCAST_TASKS[channel.id] = task

        return await reply_text(
            interaction,
            f"✅ Vote-Broadcast gestartet in {channel.mention} – alle **{every_hours}h** "
            f"{'(mit @everyone)' if ping_everyone else '(ohne @everyone)'}.\n"
            "Hinweis: Läuft **ohne DB** und **endet bei Bot-Restart** automatisch.",
            kind="success",
            ephemeral=True,
        )

    @app_commands.command(
        name="vote_broadcast_stop",
        description="(Owner) Stoppt die wiederkehrende Vote-Erinnerung in einem Kanal."
    )
    @app_commands.describe(channel="Kanal, in dem aktuell gesendet wird")
    async def vote_broadcast_stop(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if not await self._ensure_owner(interaction):
            return

        task = VOTE_BROADCAST_TASKS.pop(channel.id, None)
        if not task:
            return await reply_text(
                interaction,
                f"ℹ️ Für {channel.mention} läuft aktuell **kein** Vote-Broadcast.",
                kind="info",
                ephemeral=True,
            )

        try:
            task.cancel()
        except Exception:
            pass

        return await reply_text(
            interaction,
            f"🛑 Vote-Broadcast in {channel.mention} gestoppt.",
            kind="success",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerToolsCog(bot))