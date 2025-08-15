# bot/cogs/vc_tracking_simple.py
from __future__ import annotations
import asyncio
from datetime import datetime
from typing import Optional, Dict

import json
import discord
from discord.ext import commands
from zoneinfo import ZoneInfo

from ..services.guild_config import get_guild_cfg
from ..services.translation import translate_text_for_guild
from ..utils.replies import make_embed
from ..db import fetchrow
from ..utils.checks import GuildLangGuard

def _fmt_dur(total_seconds: int) -> str:
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _now() -> datetime:
    return datetime.now(tz=ZoneInfo("Europe/Berlin"))

class VCTrackingSimpleCog(GuildLangGuard, commands.Cog):
    """
    Simple VC-Tracking:
      - Start: sobald die erste (nicht-Bot) Person joint
      - Stop: sobald der Channel leer ist (Bots werden ignoriert)
      - Live-Embed im vc_log_channel (oder system_channel / DM-Fallback)
      - KEIN Override nötig; läuft nur, wenn Channel in vc_tracking steht UND nicht in vc_overrides
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Laufende Sessions pro Voice-Channel-ID
        # {
        #   'guild_id': int,
        #   'channel_id': int,
        #   'started_by_id': int,
        #   'started_at': datetime,
        #   'accum': {user_id: seconds},
        #   'running': {user_id: datetime_start},
        #   'message': discord.Message | None,
        #   'task': asyncio.Task | None,
        # }
        self.vc_live_sessions: Dict[int, Dict] = {}

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
            kind="info",  # blurple
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
          - Channel in vc_tracking vorhanden ist UND
          - derselbe Channel NICHT in vc_overrides konfiguriert ist.
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
            "SELECT 1 AS x FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
            member.guild.id, vc.id
        )
        if not row:
            return

        # … und darf KEIN vc_override haben (sonst übernimmt das Override-Cog)
        row_override = await fetchrow(
            "SELECT 1 AS x FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2",
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
    await bot.add_cog(VCTrackingSimpleCog(bot))