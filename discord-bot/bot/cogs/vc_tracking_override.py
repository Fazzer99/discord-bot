# bot/cogs/vc_tracking_override.py
from __future__ import annotations
import asyncio
import json
from datetime import datetime
from typing import Optional, Dict

import discord
from discord import app_commands
from discord.ext import commands
from zoneinfo import ZoneInfo

from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..services.translation import translate_text_for_guild
from ..utils.replies import make_embed, reply_text
from ..utils.checks import require_manage_guild
from ..db import fetchrow, execute, fetch


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

class VcTrackingOverrideCog(commands.Cog):
    def __init__(self, bot_: commands.Bot):
        global bot
        bot = bot_
        self.bot = bot_

    # ---------- NEU: /set_vc_override -----------------------------------
    @app_commands.command(
        name="set_vc_override",
        description="Richtet vc_override für einen Voice-Kanal ein (Override-Rollen → Ziel-Rollen)."
    )
    @require_manage_guild()
    @app_commands.describe(
        channel="Sprachkanal, für den das Override gelten soll",
        override_role1="Override-Rolle 1 (z. B. @Admin)",
        override_role2="Optional: weitere Override-Rolle",
        override_role3="Optional: weitere Override-Rolle",
        override_role4="Optional: weitere Override-Rolle",
        override_role5="Optional: weitere Override-Rolle",
        target_role1="Ziel-Rolle 1 (bekommt CONNECT an/aus)",
        target_role2="Optional: weitere Ziel-Rolle",
        target_role3="Optional: weitere Ziel-Rolle",
        target_role4="Optional: weitere Ziel-Rolle",
        target_role5="Optional: weitere Ziel-Rolle",
        log_channel="Optional: Textkanal für Live-VC-Logs (überschreibt bestehende Einstellung)"
    )
    async def set_vc_override(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        override_role1: discord.Role,
        target_role1: discord.Role,
        override_role2: Optional[discord.Role] = None,
        override_role3: Optional[discord.Role] = None,
        override_role4: Optional[discord.Role] = None,
        override_role5: Optional[discord.Role] = None,
        target_role2: Optional[discord.Role] = None,
        target_role3: Optional[discord.Role] = None,
        target_role4: Optional[discord.Role] = None,
        target_role5: Optional[discord.Role] = None,
        log_channel: Optional[discord.TextChannel] = None,
    ):
        # 1) Konflikt-Prüfung: darf nicht parallel vc_track sein
        exists_track = await fetchrow(
            "SELECT 1 FROM public.vc_tracking WHERE guild_id=$1 AND channel_id=$2",
            interaction.guild.id, channel.id
        )
        if exists_track:
            return await reply_text(
                interaction,
                f"❌ Für {channel.mention} ist bereits **vc_track** aktiv. "
                f"Bitte zuerst `/disable module:vc_track channel:{channel.name}` ausführen oder einen anderen Kanal wählen.",
                kind="error"
            )

        # 2) Rollen einsammeln
        override_ids = [r.id for r in [override_role1, override_role2, override_role3, override_role4, override_role5] if r]
        target_ids   = [r.id for r in [target_role1, target_role2, target_role3, target_role4, target_role5] if r]

        if not override_ids or not target_ids:
            return await reply_text(
                interaction,
                "❌ Bitte mindestens **eine** Override-Rolle und **eine** Ziel-Rolle angeben.",
                kind="error"
            )

        # 3) Logkanal optional aktualisieren
        if log_channel is not None:
            await update_guild_cfg(interaction.guild.id, vc_log_channel=log_channel.id)

        # 4) Upsert in vc_overrides
        await execute(
            """
            INSERT INTO public.vc_overrides (guild_id, channel_id, override_roles, target_roles)
            VALUES ($1, $2, $3::jsonb, $4::jsonb)
            ON CONFLICT (guild_id, channel_id) DO UPDATE
              SET override_roles = EXCLUDED.override_roles,
                  target_roles   = EXCLUDED.target_roles
            """,
            interaction.guild.id,
            channel.id,
            json.dumps(override_ids),
            json.dumps(target_ids),
        )

        # 5) ACK
        log_id = (await get_guild_cfg(interaction.guild.id)).get("vc_log_channel")
        log_ch = interaction.guild.get_channel(log_id) if log_id else None
        return await reply_text(
            interaction,
            f"🎉 **vc_override** aktiviert für {channel.mention}.\n"
            f"🔐 Override-Rollen: {', '.join(f'<@&{i}>' for i in override_ids)}\n"
            f"🎯 Ziel-Rollen: {', '.join(f'<@&{i}>' for i in target_ids)}\n"
            + (f"🧾 Live-Logs gehen nach {log_ch.mention}." if isinstance(log_ch, discord.TextChannel) else "ℹ️ Kein Log-Kanal gesetzt."),
            kind="success"
        )
    
    @app_commands.command(
        name="vc_tracking_override",
        description="Zeigt die aktuelle vc_override-Konfiguration dieser Guild."
    )
    @require_manage_guild()
    async def vc_tracking_override_status(self, interaction: discord.Interaction):
        """Listet alle vc_overrides (pro Sprachkanal: Override- und Ziel-Rollen)."""
        rows = await fetch(
            """
            SELECT channel_id, override_roles, target_roles
            FROM public.vc_overrides
            WHERE guild_id = $1
            ORDER BY channel_id
            """,
            interaction.guild.id,
        )

        if not rows:
            return await reply_text(
                interaction,
                "ℹ️ Es sind aktuell **keine vc_override**-Einträge konfiguriert.",
                kind="info",
                ephemeral=True,
            )

        # Helper zum Dekodieren
        def _to_list(raw):
            try:
                return json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                return []

        # Schickes Embed bauen
        emb = make_embed(
            title="🔧 vc_override – Konfiguration",
            description="Aktuelle Overrides pro Sprachkanal.",
            kind="info",
            fields=[]
        )

        # Discord erlaubt max. 25 Felder -> ggf. kürzen
        max_fields = 25
        added = 0

        for r in rows:
            if added >= max_fields:
                break

            ch = interaction.guild.get_channel(r["channel_id"])
            ch_name = ch.mention if isinstance(ch, discord.VoiceChannel) else f"<#{r['channel_id']}>"

            override_ids = _to_list(r["override_roles"])
            target_ids   = _to_list(r["target_roles"])

            def fmt_roles(ids):
                parts = []
                for rid in ids:
                    role = interaction.guild.get_role(int(rid))
                    parts.append(role.mention if role else f"<@&{rid}>")
                return ", ".join(parts) if parts else "—"

            value = (
                f"**Override-Rollen:** {fmt_roles(override_ids)}\n"
                f"**Ziel-Rollen:** {fmt_roles(target_ids)}"
           )

            emb.add_field(name=ch_name, value=value, inline=False)
            added += 1

        # Hinweis falls gekürzt
        if added < len(rows):
            emb.set_footer(text=f"… und {len(rows)-added} weitere Einträge.")

        return await interaction.response.send_message(embed=emb, ephemeral=True)    

    # ---------- Listener: Override + Live-Tracking ------------------------
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

        # 6) Rechte-Management
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

async def setup(bot):
    await bot.add_cog(VcTrackingOverrideCog(bot))