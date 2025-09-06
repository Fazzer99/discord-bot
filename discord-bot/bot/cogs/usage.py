# bot/cogs/usage.py
from __future__ import annotations
from typing import Optional, Iterable, Literal, Sequence
from datetime import datetime, timedelta, timezone
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..db import execute, fetch
from ..services.guild_config import get_guild_cfg
from ..config import settings
from ..utils.replies import make_embed, send_embed, reply_text, tracked_send

log = logging.getLogger("ignix.usage")

# ───────────────────────────── Helpers: Counting ─────────────────────────────

def _safe_len(s: Optional[str]) -> int:
    return len(s or "")

def count_embed_chars(embed: discord.Embed) -> int:
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
    if not guild_id:
        return "dm"
    try:
        cfg = await get_guild_cfg(guild_id)
        lang = str(cfg.get("lang") or "de").lower()
        return lang if lang in {"de", "en", "dm"} else "de"
    except Exception:
        return "de"


# ───────────────────── Export für replies.py (ephemeral logging) ────────────
async def log_interaction_output(
    inter: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    embeds: Optional[list[discord.Embed]] = None,
    message_type: str = "ephemeral",
) -> None:
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
            guild_id, channel_id, user_id, message_type, int(chars), lang, False, True,
        )
        log.debug("[EPH] +%s chars (gid=%s cid=%s uid=%s)", chars, guild_id, channel_id, user_id)
    except Exception as e:
        log.exception("ephemeral log failed: %r", e)


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
    now = now or datetime.now(timezone.utc)
    if range_opt == "today":
        start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now, "Heute"
    if range_opt == "yesterday":
        midnight = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight - timedelta(days=1)
        return start, midnight, "Gestern"
    if range_opt == "7d":
        return now - timedelta(days=7), now, "Letzte 7 Tage"
    if range_opt == "30d":
        return now - timedelta(days=30), now, "Letzte 30 Tage"

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
    return [True, False] if include else [False]


class UsageCog(commands.Cog):
    """(1) Logging sichtbarer Bot-Outputs  (2) /bot_usage Dashboard  (3) /usage_diag Diagnose"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        log.info("[USAGE] UsageCog geladen (listeners aktiv)")

    # 1) Sichtbare Bot-Nachrichten (Channel & DM) loggen
    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        try:
            bot_id = getattr(self.bot.user, "id", None)
            is_own_bot_msg = (bot_id is not None and msg.author.id == bot_id)
            has_interaction = getattr(msg, "interaction", None) is not None
            is_our_webhook_msg = (msg.webhook_id is not None) and (getattr(msg.author, "bot", False) or has_interaction)

            # Debug: Rohdaten
            log.debug(
                "[EVT] on_message id=%s author=%s (bot=%s) webhook_id=%s has_interaction=%s content_len=%s embeds=%s",
                getattr(msg, "id", "?"),
                getattr(getattr(msg, "author", None), "id", "?"),
                getattr(getattr(msg, "author", None), "bot", "?"),
                getattr(msg, "webhook_id", None),
                has_interaction,
                len(msg.content or ""),
                len(msg.embeds or []),
            )

            if not (is_own_bot_msg or is_our_webhook_msg):
                return

            is_dm = isinstance(msg.channel, (discord.DMChannel, discord.GroupChannel))
            guild_id = msg.guild.id if msg.guild else None
            channel_id = msg.channel.id

            # DM-Empfänger best effort
            user_id = None
            if is_dm:
                try:
                    user_id = getattr(msg.channel, "recipient", None).id
                except Exception:
                    user_id = None

            # Counting
            chars = total_message_chars(msg.content, msg.embeds)
            log.debug("[CNT] computed chars=%s (gid=%s cid=%s is_dm=%s)", chars, guild_id, channel_id, is_dm)
            if chars <= 0:
                return

            lang = await _guild_lang(guild_id)

            await execute(
                """
                INSERT INTO public.output_usage
                    (ts, guild_id, channel_id, user_id, message_type, chars, lang, is_dm, is_ephemeral)
                VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8)
                """,
                guild_id, channel_id, user_id,
                "dm" if is_dm else "channel",
                int(chars), lang, bool(is_dm), False,
            )
            log.info("[INS] +%s chars into output_usage (gid=%s cid=%s dm=%s)", chars, guild_id, channel_id, is_dm)

        except Exception as e:
            log.exception("[ERR] on_message logging failed: %r", e)

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

        total_row = await fetch(
            f"SELECT COALESCE(SUM(chars),0) AS total FROM public.output_usage WHERE {where}",
            *params
        )
        total = int(total_row[0]["total"]) if total_row else 0
        log.info("[DASH] total=%s window=%s..%s gid=%s cid=%s lang=%s dm=%s eph=%s",
                 total, start, end, gid, cid, lang_filter, dm_flags, eph_flags)

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
                g = self.bot.get_guild(int(gid_)) if gid_ is not None else None
                gname = "DM" if gid_ is None else (g.name if g else f"Guild {gid_}")
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

    # 3) Diagnose: prüft Intents, erzeugt Test-Output, schreibt Test-Row
    @app_commands.command(name="usage_diag", description="(Owner) Diagnose für Usage-Logging.")
    @app_commands.describe(post_test_message="Sichtbare Testnachricht im aktuellen Kanal posten?")
    async def usage_diag(self, interaction: discord.Interaction, post_test_message: bool = True):
        if not _owner_only(interaction.user):
            await reply_text(interaction, "❌ Nur der Bot-Owner.", kind="error", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        intents = interaction.client.intents
        intent_info = (
            f"guilds={intents.guilds}, messages={intents.messages}, "
            f"dm_messages={intents.dm_messages}, message_content={intents.message_content}, "
            f"members={intents.members}, voice_states={intents.voice_states}"
        )

        # (A) optional: sichtbare Testnachricht -> sollte on_message triggern
        if post_test_message and isinstance(interaction.channel, discord.abc.Messageable):
            try:
                emb = make_embed(title="Usage-Test", description="Dieser Post sollte geloggt werden.", kind="info")
                await tracked_send(interaction.channel, embed=emb)
                log.info("[DIAG] posted visible test message in cid=%s", interaction.channel.id)
            except Exception as e:
                log.exception("[DIAG] failed to post test message: %r", e)

        # (B) direkte Testzeile in DB inserten (um DB-Pfad zu prüfen)
        try:
            await execute(
                """
                INSERT INTO public.output_usage
                    (ts, guild_id, channel_id, user_id, message_type, chars, lang, is_dm, is_ephemeral)
                VALUES (now(), $1, $2, $3, 'diag', 7, 'de', false, false)
                """,
                interaction.guild_id, interaction.channel_id, interaction.user.id
            )
            db_ok = True
        except Exception as e:
            db_ok = False
            log.exception("[DIAG] direct insert failed: %r", e)

        # (C) Summen abfragen (letzte 10min)
        start = datetime.now(timezone.utc) - timedelta(minutes=10)
        rows = await fetch(
            "SELECT COUNT(*) AS n_rows, COALESCE(SUM(chars),0) AS sum FROM public.output_usage WHERE ts >= $1",
            start
        )
        n_rows = int(rows[0]["n_rows"]) if rows else 0
        sum_chars = int(rows[0]["sum"]) if rows else 0

        emb = make_embed(
            title="🧪 Usage Diagnose",
            kind="info",
            fields=[
                ("Intents", f"`{intent_info}`", False),
                ("DB Test-Insert", "OK ✅" if db_ok else "FEHLER ❌", False),
                ("Letzte 10 Minuten", f"Rows: **{n_rows}**, Zeichen: **{sum_chars}**", False),
                ("Hinweis", "Wenn die sichtbare Testnachricht nicht gezählt wurde, prüfe Logs [EVT]/[CNT]/[INS].", False),
            ],
        )
        await send_embed(interaction, emb, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(UsageCog(bot))