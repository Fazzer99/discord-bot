# bot/cogs/usage.py
from __future__ import annotations
from typing import Optional, Iterable, Literal, Sequence
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from ..db import execute, fetch
from ..services.guild_config import get_guild_cfg
from ..config import settings
from ..utils.replies import make_embed, send_embed, reply_text


# ───────────────────────────── Helpers: Counting ─────────────────────────────

def _safe_len(s: Optional[str]) -> int:
    return len(s or "")

def count_embed_chars(embed: discord.Embed) -> int:
    """Zählt alle sichtbaren Texte im Embed."""
    n = 0
    n += _safe_len(embed.title)
    n += _safe_len(embed.description)
    if embed.footer and getattr(embed.footer, "text", None):
        n += _safe_len(embed.footer.text)
    if embed.author and getattr(embed.author, "name", None):
        n += _safe_len(embed.author.name)
    for f in (embed.fields or []):
        n += _safe_len(f.name)
        n += _safe_len(f.value)
    return n

def total_message_chars(content: Optional[str], embeds: Iterable[discord.Embed] | None) -> int:
    total = _safe_len(content)
    if embeds:
        for e in embeds:
            total += count_embed_chars(e)
    return total

async def _guild_lang(guild_id: Optional[int]) -> str:
    """Liest die Guild-Sprache aus der Config; Fallback 'de'. Für DMs → 'dm'."""
    if not guild_id:
        return "dm"
    try:
        cfg = await get_guild_cfg(guild_id)
        lang = str(cfg.get("lang") or "de").lower()
        return lang if lang in {"de", "en", "dm"} else "de"
    except Exception:
        return "de"


# ───────────────────── Exported helper: for replies.py ──────────────────────
# (replies.py ruft das nach erfolgreichen *ephemeral* Interaction-Sends auf)

async def log_interaction_output(
    inter: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    embeds: Optional[list[discord.Embed]] = None,
    message_type: str = "ephemeral",
) -> None:
    """
    Loggt eine Interaction-Antwort (typisch: ephemeral),
    damit diese in output_usage landet (on_message sieht die ja nicht).
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
            False,  # Interactions sind keine DMs
            True,   # hier speziell: ephemeral
        )
    except Exception:
        # Logging darf niemals Antworten verhindern
        pass


# ───────────────────────────── The Usage Cog ────────────────────────────────

RangeOpt = Literal["today", "yesterday", "7d", "30d", "custom"]
BreakdownOpt = Literal["total", "by_guild", "by_channel", "by_lang", "by_type"]
LangOpt = Literal["any", "de", "en", "dm"]


def _owner_only(user: discord.abc.User) -> bool:
    return int(user.id) == int(settings.owner_id)

def _time_window(
    range_opt: RangeOpt,
    from_iso: Optional[str],
    to_iso: Optional[str],
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime, str]:
    """Gibt (start, end, label) in UTC zurück."""
    now = now or datetime.now(timezone.utc)
    if range_opt == "today":
        start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        label = "Heute"
        return start, now, label
    if range_opt == "yesterday":
        midnight = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight - timedelta(days=1)
        return start, midnight, "Gestern"
    if range_opt == "7d":
        return now - timedelta(days=7), now, "Letzte 7 Tage"
    if range_opt == "30d":
        return now - timedelta(days=30), now, "Letzte 30 Tage"

    # custom
    try:
        start = datetime.fromisoformat((from_iso or "").strip())
    except Exception:
        start = now - timedelta(days=1)
    try:
        end = datetime.fromisoformat((to_iso or "").strip())
    except Exception:
        end = now
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return start, end, "Benutzerdefiniert"

def _flag_array(include: bool) -> Sequence[bool]:
    """Für ANY(boolean[]) im SQL."""
    return [True, False] if include else [False]


class UsageCog(commands.Cog):
    """Bündelt: (1) sichtbares Output-Logging  (2) /bot_usage Dashboard."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # 1) Sichtbare Bot-Nachrichten (Channel & DM) loggen
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        # Nur Nachrichten des *eigenen* Bots
        if not msg.author.bot or not self.bot.user or msg.author.id != self.bot.user.id:
            return

        # Ephemeral gibt es hier nicht – alles hier ist sichtbar
        try:
            is_dm = isinstance(msg.channel, (discord.DMChannel, discord.GroupChannel))
            guild_id = msg.guild.id if msg.guild else None
            channel_id = msg.channel.id

            # Bei DM: Empfänger (nicht kritisch, Best-Effort)
            user_id = None
            if is_dm:
                try:
                    user_id = getattr(msg.channel, "recipient", None).id
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
            pass

    # 2) Dashboard /bot_usage (Owner)
    @app_commands.command(name="bot_usage", description="(Owner) Usage-Dashboard des Bots anzeigen.")
    @app_commands.describe(
        range="Zeitraum",
        from_iso="Start (bei custom), ISO 8601, z.B. 2025-08-04T00:00:00",
        to_iso="Ende (bei custom), ISO 8601",
        guild_id="Nur diese Guild-ID",
        channel_id="Nur dieser Channel",
        lang="Sprache filtern (any=alle)",
        include_dm="DMs mitrechnen?",
        include_ephemeral="Ephemeral-Antworten mitrechnen?",
        breakdown="Aufschlüsselung",
    )
    @app_commands.choices(
        range=[
            app_commands.Choice(name="Heute", value="today"),
            app_commands.Choice(name="Gestern", value="yesterday"),
            app_commands.Choice(name="Letzte 7 Tage", value="7d"),
            app_commands.Choice(name="Letzte 30 Tage", value="30d"),
            app_commands.Choice(name="Benutzerdefiniert", value="custom"),
        ],
        breakdown=[
            app_commands.Choice(name="Gesamt", value="total"),
            app_commands.Choice(name="Nach Guild", value="by_guild"),
            app_commands.Choice(name="Nach Channel", value="by_channel"),
            app_commands.Choice(name="Nach Sprache", value="by_lang"),
            app_commands.Choice(name="Nach Typ", value="by_type"),
        ],
        lang=[
            app_commands.Choice(name="Alle", value="any"),
            app_commands.Choice(name="Deutsch", value="de"),
            app_commands.Choice(name="Englisch", value="en"),
            app_commands.Choice(name="DM (ohne Guild)", value="dm"),
        ],
    )
    async def usage_dashboard(
        self,
        interaction: discord.Interaction,
        range: RangeOpt = "7d",
        from_iso: Optional[str] = None,
        to_iso: Optional[str] = None,
        guild_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        lang: LangOpt = "any",
        include_dm: bool = True,
        include_ephemeral: bool = True,
        breakdown: BreakdownOpt = "total",
    ):
        if not _owner_only(interaction.user):
            await reply_text(interaction, "❌ Nur der Bot-Owner darf diesen Befehl nutzen.", kind="error", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        start, end, label = _time_window(range, from_iso, to_iso)
        gid = int(guild_id) if (guild_id and guild_id.isdigit()) else None
        cid = int(channel_id) if (channel_id and channel_id.isdigit()) else None
        lang_filter = None if lang == "any" else lang
        dm_flags = _flag_array(include_dm)
        eph_flags = _flag_array(include_ephemeral)

        where = """
          ts BETWEEN $1 AND $2
          AND ($3::bigint IS NULL OR guild_id = $3)
          AND ($4::bigint IS NULL OR channel_id = $4)
          AND ($5::text   IS NULL OR lang      = $5)
          AND (is_dm = ANY($6::boolean[]))
          AND (is_ephemeral = ANY($7::boolean[]))
        """
        params = [start, end, gid, cid, lang_filter, dm_flags, eph_flags]

        # Gesamt
        total_row = await fetch(
            f"SELECT COALESCE(SUM(chars),0) AS total FROM public.output_usage WHERE {where}",
            *params
        )
        total = int(total_row[0]["total"]) if total_row else 0

        desc = (
            f"**Zeitraum:** {label}\n"
            f"**Von:** `{start.isoformat()}`\n"
            f"**Bis:** `{end.isoformat()}`\n"
            f"**Filter:** "
            f"{'Guild=' + str(gid) + ' · ' if gid else ''}"
            f"{'Channel=' + str(cid) + ' · ' if cid else ''}"
            f"{'Lang=' + lang if lang != 'any' else 'Lang=alle'} · "
            f"{'DM=ja' if include_dm else 'DM=nein'} · "
            f"{'Ephemeral=ja' if include_ephemeral else 'Ephemeral=nein'}"
        )
        head = make_embed(
            title="📊 Bot-Usage",
            description=desc,
            kind="info",
            fields=[("Gesamt-Zeichen", f"**{total:,}**", False)]
        )
        await send_embed(interaction, head, ephemeral=True)

        if breakdown == "total":
            return

        if breakdown == "by_guild":
            rows = await fetch(
                f"""
                SELECT guild_id, COALESCE(SUM(chars),0) AS sum
                FROM public.output_usage
                WHERE {where}
                GROUP BY guild_id
                ORDER BY sum DESC
                LIMIT 20
                """,
                *params
            )
            if not rows:
                await reply_text(interaction, "Keine Daten für diesen Zeitraum/Filter.", ephemeral=True)
                return
            lines = []
            for r in rows:
                gid_ = r["guild_id"]
                gname = "DM" if gid_ is None else (self.bot.get_guild(int(gid_)).name if self.bot.get_guild(int(gid_)) else f"Guild {gid_}")
                lines.append(f"• **{gname}** — `{int(r['sum']):,}`")
            emb = make_embed(title="Top Guilds (Zeichen)", description="\n".join(lines), kind="info")
            await send_embed(interaction, emb, ephemeral=True)
            return

        if breakdown == "by_channel":
            rows = await fetch(
                f"""
                SELECT channel_id, COALESCE(SUM(chars),0) AS sum
                FROM public.output_usage
                WHERE {where}
                GROUP BY channel_id
                ORDER BY sum DESC
                LIMIT 20
                """,
                *params
            )
            if not rows:
                await reply_text(interaction, "Keine Daten für diesen Zeitraum/Filter.", ephemeral=True)
                return
            lines = []
            for r in rows:
                cid_ = r["channel_id"]
                ch = None
                if cid_:
                    for g in self.bot.guilds:
                        ch = g.get_channel(int(cid_))
                        if ch:
                            break
                cname = ch.mention if isinstance(ch, discord.TextChannel) else f"Channel {cid_}"
                lines.append(f"• **{cname}** — `{int(r['sum']):,}`")
            emb = make_embed(title="Top Channels (Zeichen)", description="\n".join(lines), kind="info")
            await send_embed(interaction, emb, ephemeral=True)
            return

        if breakdown == "by_lang":
            rows = await fetch(
                f"""
                SELECT lang, COALESCE(SUM(chars),0) AS sum
                FROM public.output_usage
                WHERE {where}
                GROUP BY lang
                ORDER BY sum DESC
                """,
                *params
            )
            lines = [f"• **{r['lang'] or '—'}** — `{int(r['sum']):,}`" for r in rows] or ["—"]
            emb = make_embed(title="Nach Sprache", description="\n".join(lines), kind="info")
            await send_embed(interaction, emb, ephemeral=True)
            return

        if breakdown == "by_type":
            rows = await fetch(
                f"""
                SELECT message_type, COALESCE(SUM(chars),0) AS sum
                FROM public.output_usage
                WHERE {where}
                GROUP BY message_type
                ORDER BY sum DESC
                """,
                *params
            )
            lines = [f"• **{r['message_type']}** — `{int(r['sum']):,}`" for r in rows] or ["—"]
            emb = make_embed(title="Nach Nachrichtentyp", description="\n".join(lines), kind="info")
            await send_embed(interaction, emb, ephemeral=True)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(UsageCog(bot))