# bot/cogs/vc_tracking.py
from __future__ import annotations
import asyncio
import json
from datetime import datetime
from typing import Optional, Dict

import discord
from discord.ext import commands
from zoneinfo import ZoneInfo

from ..services.guild_config import get_guild_cfg
from ..services.translation import translate_text_for_guild
from ..utils.replies import make_embed
from ..db import fetchrow
from ..utils.checks import GuildLangGuard

# Laufende Sessions pro Voice-Channel
# Struktur pro VC-ID:
# {
#   'guild_id': int,
#   'channel_id': int,
#   'started_by_id': int,
#   'started_at': datetime,
#   'accum': {user_id: seconds},
#   'running': {user_id: datetime_start},
#   'message': discord.Message | None,
#   'task': asyncio.Task | None,
#   'override_ids': list[int],
# }
vc_live_sessions: Dict[int, Dict] = {}

def _fmt_dur(total_seconds: int) -> str:
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _now() -> datetime:
    return datetime.now(tz=ZoneInfo("Europe/Berlin"))

async def _render_embed_payload(session: dict) -> discord.Embed:
    """
    Baut ein (bereits übersetztes) Embed für die aktuelle Session.
    Titel/Labels/Texte werden per DeepL in die Guild-Sprache übersetzt.
    """
    # Objekte besorgen
    guild = discord.utils.get(bot.guilds, id=session["guild_id"])
    vc: Optional[discord.VoiceChannel] = guild.get_channel(session["channel_id"]) if guild else None
    started_by: Optional[discord.Member] = guild.get_member(session["started_by_id"]) if guild else None

    now = _now()
    totals: Dict[int, int] = {}

    # Gesammelte Sekunden
    for uid, secs in session["accum"].items():
        totals[uid] = secs

    # Laufende Zeiten addieren
    for uid, t0 in session["running"].items():
        add = int((now - t0).total_seconds())
        totals[uid] = totals.get(uid, 0) + max(0, add)

    # Zeilen sortiert (Top zuerst)
    lines = []
    for uid, secs in sorted(totals.items(), key=lambda x: x[1], reverse=True):
        member = guild.get_member(uid) if guild else None
        name = member.display_name if member else f"User {uid}"
        lines.append(f"• **{name}** – `{_fmt_dur(secs)}`")

    # Titel/Labels (DE → EN via DeepL je nach Guild)
    title_live_de = "🎙️ Voice-Session (LIVE)"
    title_final_de = "✅ Voice-Session (Final)"
    started_de = "Gestartet"
    channel_de = "Channel"
    triggered_de = "Getriggert von"
    presence_de = "Anwesenheit"
    footer_de = "Die Liste aktualisiert sich live, solange eine Override-Rolle im Channel ist."
    # Übersetzen
    title_de = title_live_de if session.get("task") else title_final_de
    title = await translate_text_for_guild(session["guild_id"], title_de)
    lbl_started = await translate_text_for_guild(session["guild_id"], started_de)
    lbl_channel = await translate_text_for_guild(session["guild_id"], channel_de)
    lbl_triggered = await translate_text_for_guild(session["guild_id"], triggered_de)
    lbl_presence = await translate_text_for_guild(session["guild_id"], presence_de)
    footer = await translate_text_for_guild(session["guild_id"], footer_de)

    emb = make_embed(
        title=title,
        description=None,
        kind="info",  # blurple
        fields=[]
    )
    if vc:
        emb.add_field(name=lbl_channel, value=vc.mention, inline=True)
    if started_by:
        emb.add_field(name=lbl_triggered, value=started_by.mention, inline=True)

    started_at = session["started_at"]
    emb.add_field(
        name=lbl_started,
        value=started_at.strftime("%d.%m.%Y %H:%M:%S"),
        inline=True
    )
    emb.add_field(name=lbl_presence, value=("\n".join(lines) if lines else "—"), inline=False)
    emb.set_footer(text=footer)
    return emb

async def _update_live_message(session: dict):
    try:
        while session.get("task") is not None:
            msg: Optional[discord.Message] = session.get("message")
            if msg:
                emb = await _render_embed_payload(session)
                try:
                    await msg.edit(embed=emb)
                except discord.NotFound:
                    break
            await asyncio.sleep(5)
    finally:
        session["task"] = None

async def _start_or_attach_session(member: discord.Member, vc: discord.VoiceChannel, override_ids: list[int]):
    sid = vc.id
    now = _now()
    sess = vc_live_sessions.get(sid)

    # Log-Kanal aus guild_settings (Spalte: vc_log_channel)
    cfg = await get_guild_cfg(member.guild.id)
    log_id = cfg.get("vc_log_channel")
    log_channel = member.guild.get_channel(log_id) if log_id else None

    if sess is None:
        sess = {
            "guild_id": member.guild.id,
            "channel_id": vc.id,
            "started_by_id": member.id,
            "started_at": now,
            "accum": {},
            "running": {},
            "message": None,
            "task": None,
            "override_ids": override_ids,
        }
        vc_live_sessions[sid] = sess

        # Zielkanal bestimmen (nie in Voice posten)
        target_channel: Optional[discord.TextChannel] = None
        if isinstance(log_channel, discord.TextChannel):
            target_channel = log_channel
        elif member.guild.system_channel:
            target_channel = member.guild.system_channel

        # Erstes Embed senden
        emb = await _render_embed_payload(sess)
        msg: Optional[discord.Message] = None
        if target_channel is not None:
            msg = await target_channel.send(embed=emb)
        else:
            # Fallback: DM an Trigger
            try:
                dm = await member.create_dm()
                msg = await dm.send(embed=emb)
            except Exception:
                msg = None

        sess["message"] = msg
        sess["task"] = bot.loop.create_task(_update_live_message(sess))

    # Member laufend markieren (Re-Join zählt weiter)
    if member.id not in sess["running"]:
        sess["running"][member.id] = now
    sess["accum"].setdefault(member.id, 0)

async def _handle_leave(member: discord.Member, vc: discord.VoiceChannel, override_ids: list[int]):
    sid = vc.id
    sess = vc_live_sessions.get(sid)
    if not sess:
        return

    t0 = sess["running"].pop(member.id, None)
    if t0:
        add = int((_now() - t0).total_seconds())
        if add > 0:
            sess["accum"][member.id] = sess["accum"].get(member.id, 0) + add

    # Ist noch eine Override-Rolle im Channel?
    still_override = any(any(r.id in override_ids for r in m.roles) for m in vc.members)
    if still_override:
        if sess.get("message"):
            try:
                emb = await _render_embed_payload(sess)
                await sess["message"].edit(embed=emb)
            except discord.NotFound:
                pass
        return

    # Session finalisieren: Restzeiten addieren
    now = _now()
    for uid, t0 in list(sess["running"].items()):
        add = int((now - t0).total_seconds())
        sess["accum"][uid] = sess["accum"].get(uid, 0) + max(0, add)
    sess["running"].clear()

    # Live-Task stoppen
    task = sess.get("task")
    if task:
        task.cancel()
        sess["task"] = None

    # Finales Embed (Titel/Footers anpassen)
    if sess.get("message"):
        try:
            final_emb = await _render_embed_payload(sess)
            # Titel/Footers bereits übersetzt, nur Inhalte ändern:
            title_final_de = "🧾 Voice-Session (Abschluss)"
            footer_final_de = "Session beendet – letzte Override-Rolle hat den Channel verlassen."
            final_emb.title = await translate_text_for_guild(sess["guild_id"], title_final_de)
            final_emb.set_footer(text=await translate_text_for_guild(sess["guild_id"], footer_final_de))
            await sess["message"].edit(embed=final_emb)
        except discord.NotFound:
            pass

    vc_live_sessions.pop(sid, None)

class VcTrackingOverrideCog(GuildLangGuard, commands.Cog):
    def __init__(self, bot_: commands.Bot):
        global bot
        bot = bot_
        self.bot = bot_

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """
        Kombinierter Listener:
          - vc_override: setzt CONNECT-Rechte für target_roles abhängig von override_roles.
          - Live-Tracking: startet/aktualisiert/endet eine Session mit Live-Embed im vc_log_channel.
        """
        # 1) Nur bei echtem Join oder Leave
        joined = before.channel is None and after.channel is not None
        left   = before.channel is not None and after.channel is None
        if not (joined or left):
            return

        # 2) Betroffenen Channel ermitteln
        vc: Optional[discord.VoiceChannel] = after.channel if joined else before.channel
        if vc is None:
            return

        # 3) Override-Config für genau diesen Channel auslesen
        row = await fetchrow(
            """
            SELECT override_roles, target_roles
              FROM vc_overrides
             WHERE guild_id   = $1
               AND channel_id = $2
            """,
            member.guild.id,
            vc.id
        )
        if not row:
            return  # kein Override für diesen Channel

        # 4) JSONB → Python-Listen
        def _to_list(raw):
            try:
                return json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                return []

        override_ids = _to_list(row["override_roles"])
        target_ids   = _to_list(row["target_roles"])
        if not override_ids or not target_ids:
            return  # schlechte/fehlende Konfiguration

        # 5) Prüfen, ob der Member eine Override-Rolle hat
        member_is_override = any(r.id in override_ids for r in member.roles)

        # 6) Rechte-Management (1:1 aus deinem Code)
        if joined:
            for rid in target_ids:
                role = member.guild.get_role(rid)
                if role:
                    over = vc.overwrites_for(role)
                    await vc.set_permissions(
                        role,
                        connect=True,
                        view_channel=over.view_channel
                    )
        elif left:
            # nur sperren, wenn letzte Override-Person gegangen ist
            still_override = any(any(r.id in override_ids for r in m.roles) for m in vc.members)
            if not still_override:
                for rid in target_ids:
                    role = member.guild.get_role(rid)
                    if role:
                        over = vc.overwrites_for(role)
                        await vc.set_permissions(
                            role,
                            connect=False,
                            view_channel=over.view_channel
                        )

        # 7) Live-Tracking
        # JOIN
        if joined:
            if member.bot:
                return
            if member_is_override:
                await _start_or_attach_session(member, vc, override_ids)
            else:
                # Kein Override: nur anhängen, falls bereits Session läuft
                sess = vc_live_sessions.get(vc.id)
                if sess is not None:
                    now = _now()
                    if member.id not in sess["running"]:
                        sess["running"][member.id] = now
                    sess["accum"].setdefault(member.id, 0)
                    if sess.get("message"):
                        try:
                            emb = await _render_embed_payload(sess)
                            await sess["message"].edit(embed=emb)
                        except discord.NotFound:
                            pass
            return

        # LEAVE
        if left:
            if vc.id not in vc_live_sessions:
                return
            await _handle_leave(member, vc, override_ids)

async def setup(bot: commands.Bot):
    await bot.add_cog(VCTrackingOverrideCog(bot))