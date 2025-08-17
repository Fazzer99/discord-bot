# bot/cogs/vc_tracking_simple.py
from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Optional, Dict

import discord
from discord import app_commands
from discord.ext import commands
from zoneinfo import ZoneInfo

from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..services.translation import translate_text_for_guild
from ..utils.replies import make_embed, reply_text, reply_success, reply_error
from ..utils.checks import require_manage_guild
from ..db import fetchrow, fetch, execute

def _fmt_dur(total_seconds: int) -> str:
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _now() -> datetime:
    return datetime.now(tz=ZoneInfo("Europe/Berlin"))

class VcTrackingSimpleCog(commands.Cog):
    """
    Simple VC-Tracking:
      - Start: sobald die erste (nicht-Bot) Person joint
      - Stop: sobald der Channel leer ist (Bots ignorieren)
      - Live-Embed im vc_log_channel (oder system_channel / DM-Fallback)
      - Läuft nur, wenn Channel in public.vc_tracking steht UND NICHT in public.vc_overrides
      - Achtung: Tabelle public.vc_tracking hat NUR (guild_id, channel_id)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Laufende Sessions pro Voice-Channel-ID
        self.vc_live_sessions: Dict[int, Dict] = {}

    # ---------- Slash-Commands (neu) ----------

    @app_commands.command(
        name="set_vc_tracking",
        description="Aktiviere einfaches VC-Tracking für einen Sprachkanal (optional: Log-Kanal setzen)."
    )
    @require_manage_guild()
    @app_commands.describe(
        channel="Sprachkanal, der getrackt werden soll",
        log_channel="(Optional) Textkanal für Live-Logs; wenn leer, wird der bestehende genutzt"
    )
    async def set_vc_tracking(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        log_channel: Optional[discord.TextChannel] = None,
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        gid = interaction.guild.id

        # 1) darf NICHT parallel vc_override sein
        exists_override = await fetchrow(
            "SELECT 1 FROM public.vc_overrides WHERE guild_id=$1 AND channel_id=$2",
            gid, channel.id
        )
        if exists_override:
            return await reply_error(
                interaction,
                f"❌ Für {channel.mention} ist bereits **vc_override** aktiv. "
                f"Bitte zuerst `/disable module:vc_override channel:{channel.name}` ausführen oder einen anderen Kanal wählen."
            )

        # 2) Log-Kanal-Handling
        cfg = await get_guild_cfg(gid)
        current_log_id = cfg.get("vc_log_channel")
        current_log_ch = interaction.guild.get_channel(current_log_id) if current_log_id else None

        # Wenn Parameter gesetzt → überschreiben
        final_log_ch: Optional[discord.TextChannel] = None
        if log_channel is not None:
            final_log_ch = log_channel
            await update_guild_cfg(gid, vc_log_channel=log_channel.id)
        else:
            # Kein Parameter: vorhandenen nutzen, sonst Fehlermeldung
            if isinstance(current_log_ch, discord.TextChannel):
                final_log_ch = current_log_ch
            else:
                return await reply_error(
                    interaction,
                    "❌ Es ist kein Log-Kanal gesetzt. Bitte gib `log_channel` an oder setze ihn vorher (z. B. einmalig bei einem anderen Setup)."
                )

        # 3) Bereits aktiv?
        exists = await fetchrow(
            "SELECT 1 FROM public.vc_tracking WHERE guild_id=$1 AND channel_id=$2",
            gid, channel.id
        )
        if exists:
            return await reply_text(
                interaction,
                f"ℹ️ **VC-Tracking** ist für {channel.mention} bereits aktiv. (Log-Kanal: {final_log_ch.mention})",
                kind="info",
            )

        # 4) Aktivieren
        await execute(
            "INSERT INTO public.vc_tracking (guild_id, channel_id) VALUES ($1, $2)",
            gid, channel.id
        )

        return await reply_success(
            interaction,
            f"🎉 **vc_track** aktiviert für {channel.mention}.\n"
            f"🧾 Live-Logs gehen nach {final_log_ch.mention}."
        )

    @app_commands.command(
        name="vc_tracking",
        description="Zeigt alle Kanäle mit einfachem VC-Tracking und den aktuellen Log-Kanal."
    )
    @require_manage_guild()
    async def vc_tracking_status(self, interaction: discord.Interaction):
        gid = interaction.guild.id

        rows = await fetch(
            """
            SELECT channel_id
              FROM public.vc_tracking
             WHERE guild_id = $1
             ORDER BY channel_id
            """,
            gid
        )

        cfg = await get_guild_cfg(gid)
        log_id = cfg.get("vc_log_channel")
        log_ch = interaction.guild.get_channel(log_id) if log_id else None

        if not rows:
            desc = (
                f"**Log-Kanal:** {log_ch.mention if isinstance(log_ch, discord.TextChannel) else '—'}\n"
                f"**Getrackte Sprachkanäle:** —"
            )
            emb = make_embed(title="🔧 VC-Tracking – Konfiguration", description=desc, kind="info", fields=[])
            return await interaction.response.send_message(embed=emb, ephemeral=True)

        # Embed mit Liste der Channels
        emb = make_embed(
            title="🔧 VC-Tracking – Konfiguration",
            description=f"**Log-Kanal:** {log_ch.mention if isinstance(log_ch, discord.TextChannel) else '—'}",
            kind="info",
            fields=[]
        )

        # Discord: max. 25 Felder
        added = 0
        for r in rows:
            if added >= 25:
                break
            ch = interaction.guild.get_channel(r["channel_id"])
            ch_name = ch.mention if isinstance(ch, discord.VoiceChannel) else f"<#{r['channel_id']}>"
            emb.add_field(name=ch_name, value="aktiv", inline=False)
            added += 1

        if added < len(rows):
            emb.set_footer(text=f"… und {len(rows) - added} weitere Einträge.")

        return await interaction.response.send_message(embed=emb, ephemeral=True)

    # ---------- RENDER / UPDATE ----------

    async def _render_embed_payload_simple(self, session: dict) -> discord.Embed:
        guild = self.bot.get_guild(session["guild_id"])
        vc: Optional[discord.VoiceChannel] = guild.get_channel(session["channel_id"]) if guild else None
        started_by: Optional[discord.Member] = guild.get_member(session["started_by_id"]) if guild else None

        now = _now()
        totals = {uid: secs for uid, secs in session["accum"].items()}
        for uid, t0 in session["running"].items():
            totals[uid] = totals.get(uid, 0) + max(0, int((now - t0).total_seconds()))

        lines = []
        for uid, secs in sorted(totals.items(), key=lambda x: x[1], reverse=True):
            m = guild.get_member(uid) if guild else None
            name = m.display_name if m else f"User {uid}"
            lines.append(f"• **{name}** – `{_fmt_dur(secs)}`")

        # Labels (DE → ggf. EN via DeepL je Guild)
        title_live_de   = "🎙️ Voice-Session (LIVE)"
        title_final_de  = "✅ Voice-Session (Final)"
        lbl_channel_de  = "Channel"
        lbl_by_de       = "Getriggert von"
        lbl_started_de  = "Gestartet"
        lbl_present_de  = "Anwesenheit"
        footer_live_de  = "Die Liste aktualisiert sich live, solange Personen im Channel sind."

        title_de = title_live_de if session.get("task") else title_final_de
        title    = await translate_text_for_guild(session["guild_id"], title_de)
        lbl_channel = await translate_text_for_guild(session["guild_id"], lbl_channel_de)
        lbl_by      = await translate_text_for_guild(session["guild_id"], lbl_by_de)
        lbl_started = await translate_text_for_guild(session["guild_id"], lbl_started_de)
        lbl_present = await translate_text_for_guild(session["guild_id"], lbl_present_de)
        footer_live = await translate_text_for_guild(session["guild_id"], footer_live_de)

        emb = make_embed(
            title=title,
            description=None,
            kind="info",
            fields=[]
        )
        if vc:
            emb.add_field(name=lbl_channel, value=vc.mention, inline=True)
        if started_by:
            emb.add_field(name=lbl_by, value=started_by.mention, inline=True)
        emb.add_field(name=lbl_started, value=session["started_at"].strftime("%d.%m.%Y %H:%M:%S"), inline=True)
        emb.add_field(name=lbl_present, value=("\n".join(lines) if lines else "—"), inline=False)
        emb.set_footer(text=footer_live)
        return emb

    async def _update_live_message_simple(self, session: dict):
        try:
            while session.get("task") is not None:
                msg: Optional[discord.Message] = session.get("message")
                if msg:
                    try:
                        emb = await self._render_embed_payload_simple(session)
                        await msg.edit(embed=emb)
                    except discord.NotFound:
                        break
                await asyncio.sleep(5)
        finally:
            session["task"] = None

    # ---------- SESSION CONTROL ----------

    async def _start_or_attach_session_simple(self, member: discord.Member, vc: discord.VoiceChannel):
        sid = vc.id
        now = _now()
        sess = self.vc_live_sessions.get(sid)

        cfg = await get_guild_cfg(member.guild.id)
        log_id = cfg.get("vc_log_channel")
        log_channel = member.guild.get_channel(log_id) if log_id else None

        if sess is None:
            sess = {
                "guild_id": member.guild.id,
                "channel_id": vc.id,
                "started_by_id": member.id,  # erster Joiner
                "started_at": now,
                "accum": {},
                "running": {},
                "message": None,
                "task": None,
            }
            self.vc_live_sessions[sid] = sess

            # Zielkanal für das Live-Embed bestimmen (nie in Voice posten)
            target_channel: Optional[discord.TextChannel] = None
            if isinstance(log_channel, discord.TextChannel):
                target_channel = log_channel
            elif member.guild.system_channel:
                target_channel = member.guild.system_channel

            # Erstes Embed senden (mit Fallback DM)
            emb = await self._render_embed_payload_simple(sess)
            msg: Optional[discord.Message] = None
            if target_channel is not None:
                msg = await target_channel.send(embed=emb)
            else:
                try:
                    dm = await member.create_dm()
                    msg = await dm.send(embed=emb)
                except Exception:
                    msg = None

            sess["message"] = msg
            sess["task"] = self.bot.loop.create_task(self._update_live_message_simple(sess))

            # Alle bereits im VC (ohne Bots) aufnehmen
            now = _now()
            for m in vc.members:
                if m.bot:
                    continue
                if m.id not in sess["running"]:
                    sess["running"][m.id] = now
                sess["accum"].setdefault(m.id, 0)

        # Mitglied anhängen (Re-Join zählt weiter)
        if member.id not in sess["running"]:
            sess["running"][member.id] = now
        sess["accum"].setdefault(member.id, 0)

    async def _handle_leave_simple(self, member: discord.Member, vc: discord.VoiceChannel):
        sid = vc.id
        sess = self.vc_live_sessions.get(sid)
        if not sess:
            return

        t0 = sess["running"].pop(member.id, None)
        if t0:
            add = int((_now() - t0).total_seconds())
            if add > 0:
                sess["accum"][member.id] = sess["accum"].get(member.id, 0) + add

        # noch Personen im VC? (Bots ignorieren)
        if any(not m.bot for m in vc.members):
            if sess.get("message"):
                try:
                    emb = await self._render_embed_payload_simple(sess)
                    await sess["message"].edit(embed=emb)
                except discord.NotFound:
                    pass
            return

        # finalisieren (Channel leer)
        now = _now()
        for uid, t0 in list(sess["running"].items()):
            sess["accum"][uid] = sess["accum"].get(uid, 0) + max(0, int((now - t0).total_seconds()))
        sess["running"].clear()

        task = sess.get("task")
        if task:
            task.cancel()
            sess["task"] = None

        if sess.get("message"):
            try:
                final = await self._render_embed_payload_simple(sess)
                # finale Beschriftungen (übersetzt)
                title_final_de = "🧾 Voice-Session (Abschluss)"
                footer_final_de = "Session beendet – der Channel ist jetzt leer."
                final.title = await translate_text_for_guild(sess["guild_id"], title_final_de)
                final.set_footer(text=await translate_text_for_guild(sess["guild_id"], footer_final_de))
                await sess["message"].edit(embed=final)
            except discord.NotFound:
                pass

        self.vc_live_sessions.pop(sid, None)

    # ---------- Listener ----------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """
        Simple Tracking feuert NUR, wenn:
          - Channel in public.vc_tracking vorhanden ist UND
          - derselbe Channel NICHT in public.vc_overrides konfiguriert ist.
        """
        joined = before.channel is None and after.channel is not None
        left   = before.channel is not None and after.channel is None
        if not (joined or left):
            return

        vc: Optional[discord.VoiceChannel] = after.channel if joined else before.channel
        if vc is None:
            return

        # Kanal muss in vc_tracking stehen …
        row = await fetchrow(
            "SELECT 1 FROM public.vc_tracking WHERE guild_id=$1 AND channel_id=$2",
            member.guild.id, vc.id
        )
        if not row:
            return

        # … und darf KEIN vc_override haben (sonst übernimmt das Override-Cog)
        row_override = await fetchrow(
            "SELECT 1 FROM public.vc_overrides WHERE guild_id=$1 AND channel_id=$2",
            member.guild.id, vc.id
        )
        if row_override:
            return

        # JOIN
        if joined:
            if member.bot:
                return  # Bots starten keine Session
            await self._start_or_attach_session_simple(member, vc)
            return

        # LEAVE
        if left and (vc.id in self.vc_live_sessions):
            await self._handle_leave_simple(member, vc)

async def setup(bot: commands.Bot):
    await bot.add_cog(VcTrackingSimpleCog(bot))