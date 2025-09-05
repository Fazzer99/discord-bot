# bot/cogs/moderation.py
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..utils.checks import require_manage_channels
from ..utils.replies import reply_text, make_embed, send_embed
from ..services.guild_config import get_guild_cfg
from ..services.translation import translate_text_for_guild
from ..db import fetch, execute  # <-- DB für persistente Jobs

lock_tasks: dict[int, asyncio.Task] = {}
CHECK_INTERVAL = 20  # Sekunden für den Scheduler-Loop

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # integrierter Scheduler
        self.scan_lock_jobs.start()

    def cog_unload(self):
        try:
            self.scan_lock_jobs.cancel()
        except Exception:
            pass

    # ----------------------------- Helpers (TZ) -----------------------------

    @staticmethod
    def _get_tz_minutes(cfg: dict) -> int:
        """
        Erwartet in cfg['tz'] einen Minuten-Offset zu UTC (z.B. +120 für UTC+2).
        Fällt robust auf 0 zurück, wenn nichts gesetzt ist.
        """
        raw = cfg.get("tz")
        try:
            return int(str(raw).strip())
        except Exception:
            return 0

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_local(dt_utc: datetime, tz_minutes: int) -> datetime:
        """UTC -> lokale naive Zeit (kein tzinfo), basierend auf Minutenoffset."""
        return (dt_utc + timedelta(minutes=tz_minutes)).replace(tzinfo=None)

    @staticmethod
    def _local_to_utc(dt_local_naive: datetime, tz_minutes: int) -> datetime:
        """Lokale naive Zeit -> UTC-aware."""
        return (dt_local_naive - timedelta(minutes=tz_minutes)).replace(tzinfo=timezone.utc)

    # ------------------------- Permission-Helpers --------------------------

    @staticmethod
    def _private_info(ch: discord.abc.GuildChannel) -> tuple[bool, list[discord.Role]]:
        """Ist der Kanal privat? Wenn ja, welche Rollen haben View?"""
        if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
            return False, []
        everyone = ch.guild.default_role
        priv_over = ch.overwrites_for(everyone)
        is_priv = (priv_over.view_channel is False)
        private_roles: list[discord.Role] = []
        if is_priv:
            for role_obj, over in ch.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)
        return is_priv, private_roles

    async def _apply_lock(self, ch: discord.TextChannel | discord.VoiceChannel):
        """Setzt die Sperre (idempotent)."""
        everyone = ch.guild.default_role
        is_priv, private_roles = self._private_info(ch)

        if isinstance(ch, discord.TextChannel):
            if is_priv:
                for r in private_roles:
                    await ch.set_permissions(r, send_messages=False, view_channel=True)
            else:
                await ch.set_permissions(everyone, send_messages=False)
        else:
            if is_priv:
                for r in private_roles:
                    await ch.set_permissions(r, connect=False, speak=False, view_channel=True)
            else:
                await ch.set_permissions(everyone, connect=False, speak=False)
            # vorsichtshalber alle kicken
            for m in list(ch.members):
                try:
                    await m.move_to(None)
                except Exception:
                    pass

    async def _apply_unlock(self, ch: discord.TextChannel | discord.VoiceChannel):
        """Hebt die Sperre auf (idempotent)."""
        everyone = ch.guild.default_role
        is_priv, private_roles = self._private_info(ch)

        if isinstance(ch, discord.TextChannel):
            if is_priv:
                for r in private_roles:
                    await ch.set_permissions(r, send_messages=None, view_channel=True)
            else:
                await ch.set_permissions(everyone, send_messages=None)
        else:
            if is_priv:
                for r in private_roles:
                    await ch.set_permissions(r, connect=None, speak=None, view_channel=True)
            else:
                await ch.set_permissions(everyone, connect=None, speak=None)

    async def _notify_locked(self, ch: discord.TextChannel | discord.VoiceChannel, guild_id: int, display_time: str, duration: int):
        cfg = await get_guild_cfg(guild_id)
        tmpl_lock = (cfg.get("templates") or {}).get(
            "lock",
            "🔒 Kanal {channel} gesperrt um {time} für {duration} Minuten 🚫"
        )
        msg_de = tmpl_lock.format(channel=ch.mention, time=display_time, duration=duration)
        msg = await translate_text_for_guild(guild_id, msg_de)
        emb = make_embed(title="🔒 Lock aktiviert", description=msg, kind="warning")
        try:
            await send_embed(ch, emb)
        except Exception:
            pass

    async def _notify_unlocked(self, ch: discord.TextChannel | discord.VoiceChannel, guild_id: int):
        txt = await translate_text_for_guild(guild_id, "🔓 Kanal automatisch entsperrt – viel Spaß! 🎉")
        emb_un = make_embed(title="🔓 Unlock", description=txt, kind="success")
        try:
            await send_embed(ch, emb_un)
        except Exception:
            pass

    # -------------------------------- lock ---------------------------------

    @app_commands.command(
        name="lock",
        description="Sperrt einen oder mehrere Kanäle ab Zeitpunkt HH:MM für X Minuten."
    )
    @require_manage_channels()
    @app_commands.describe(
        channel="Text- oder Voice-Kanal",
        channel2="Optional: weiterer Kanal",
        channel3="Optional: weiterer Kanal",
        channel4="Optional: weiterer Kanal",
        channel5="Optional: weiterer Kanal",
        channel6="Optional: weiterer Kanal",
        channel7="Optional: weiterer Kanal",
        channel8="Optional: weiterer Kanal",
        channel9="Optional: weiterer Kanal",
        channel10="Optional: weiterer Kanal",
        start_time="Startzeit im Format HH:MM (Server-Zeit)",
        duration="Dauer in Minuten"
    )
    async def lock(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel,
        start_time: str,
        duration: int,
        channel2: Optional[discord.abc.GuildChannel] = None,
        channel3: Optional[discord.abc.GuildChannel] = None,
        channel4: Optional[discord.abc.GuildChannel] = None,
        channel5: Optional[discord.abc.GuildChannel] = None,
        channel6: Optional[discord.abc.GuildChannel] = None,
        channel7: Optional[discord.abc.GuildChannel] = None,
        channel8: Optional[discord.abc.GuildChannel] = None,
        channel9: Optional[discord.abc.GuildChannel] = None,
        channel10: Optional[discord.abc.GuildChannel] = None,
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if duration <= 0:
            return await reply_text(interaction, "❌ Ungültige Dauer.", kind="error", ephemeral=True)

        # HH:MM parsen (lokale Serverzeit)
        try:
            hour, minute = [int(x) for x in start_time.split(":")]
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except Exception:
            return await reply_text(
                interaction, "❌ Ungültiges Format. Bitte `HH:MM` im 24h-Format.",
                kind="error", ephemeral=True
            )

        # Alle eingegebenen Kanäle einsammeln und säubern
        raw_channels = [channel, channel2, channel3, channel4, channel5, channel6, channel7, channel8, channel9, channel10]
        sel: list[discord.TextChannel | discord.VoiceChannel] = []
        seen: set[int] = set()
        for ch in raw_channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel)) and ch.guild.id == interaction.guild.id:
                if ch.id not in seen:
                    seen.add(ch.id)
                    sel.append(ch)

        if not sel:
            return await reply_text(interaction, "❌ Kein gültiger Kanal ausgewählt.", kind="error", ephemeral=True)

        # Guild-Config inkl. TZ-Offset (Minuten)
        cfg = await get_guild_cfg(interaction.guild.id)
        tz_minutes = self._get_tz_minutes(cfg)

        # Aktuelle Zeiten
        now_utc = self._utc_now()
        now_local = self._to_local(now_utc, tz_minutes)  # naive lokale Zeit

        # Geplante lokale Zeit heute/morgen
        local_run = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local_run <= now_local:
            local_run += timedelta(days=1)

        # Für Scheduling: in UTC umrechnen
        run_at_utc = self._local_to_utc(local_run, tz_minutes)

        display_time = local_run.strftime("%H:%M")
        scheduled_mentions: list[str] = []

        for ch in sel:
            # vorhandene In-Memory-Tasks abbrechen (sollten wir nicht mehr nutzen)
            if ch.id in lock_tasks:
                try:
                    lock_tasks[ch.id].cancel()
                except Exception:
                    pass
                lock_tasks.pop(ch.id, None)

            # Persistenten Job speichern/überschreiben
            await execute(
                """
                INSERT INTO public.lock_jobs (guild_id, channel_id, run_at, duration_minutes, created_by, status)
                VALUES ($1, $2, $3, $4, $5, 'pending')
                ON CONFLICT (guild_id, channel_id)
                DO UPDATE SET run_at = EXCLUDED.run_at,
                              duration_minutes = EXCLUDED.duration_minutes,
                              created_by = EXCLUDED.created_by,
                              status = 'pending',
                              started_at = NULL,
                              ends_at = NULL
                """,
                interaction.guild.id, ch.id, run_at_utc, int(duration), interaction.user.id
            )

            # Wenn Start binnen 5s fällig ist -> sofort ausführen (einmalig) und Job auf running setzen
            if (run_at_utc - now_utc).total_seconds() <= 5:
                await self._apply_lock(ch)
                await self._notify_locked(ch, interaction.guild.id, display_time="jetzt", duration=duration)
                await execute(
                    """
                    UPDATE public.lock_jobs
                    SET status='running', started_at=$3, ends_at=$4
                    WHERE guild_id=$1 AND channel_id=$2
                    """,
                    interaction.guild.id, ch.id, now_utc, now_utc + timedelta(minutes=duration)
                )

            scheduled_mentions.append(ch.mention)

        # Bestätigung
        return await reply_text(
            interaction,
            f"⏰ Geplante Sperre um **{display_time}** für **{duration}** Minuten.\n"
            f"**Kanäle:** {', '.join(scheduled_mentions)}",
            kind="info",
            ephemeral=True,
        )

    # -------------------------------- unlock -------------------------------

    @app_commands.command(
        name="unlock",
        description="Hebt die Sperre eines oder mehrerer Kanäle sofort auf."
    )
    @require_manage_channels()
    @app_commands.describe(
        channel="Text- oder Voice-Kanal",
        channel2="Optional: weiterer Kanal",
        channel3="Optional: weiterer Kanal",
        channel4="Optional: weiterer Kanal",
        channel5="Optional: weiterer Kanal",
        channel6="Optional: weiterer Kanal",
        channel7="Optional: weiterer Kanal",
        channel8="Optional: weiterer Kanal",
        channel9="Optional: weiterer Kanal",
        channel10="Optional: weiterer Kanal",
    )
    async def unlock(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel,
        channel2: Optional[discord.abc.GuildChannel] = None,
        channel3: Optional[discord.abc.GuildChannel] = None,
        channel4: Optional[discord.abc.GuildChannel] = None,
        channel5: Optional[discord.abc.GuildChannel] = None,
        channel6: Optional[discord.abc.GuildChannel] = None,
        channel7: Optional[discord.abc.GuildChannel] = None,
        channel8: Optional[discord.abc.GuildChannel] = None,
        channel9: Optional[discord.abc.GuildChannel] = None,
        channel10: Optional[discord.abc.GuildChannel] = None,
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        raw_channels = [channel, channel2, channel3, channel4, channel5, channel6, channel7, channel8, channel9, channel10]
        targets: list[discord.TextChannel | discord.VoiceChannel] = []
        seen: set[int] = set()
        for ch in raw_channels:
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel)) and ch.guild.id == interaction.guild.id:
                if ch.id not in seen:
                    seen.add(ch.id)
                    targets.append(ch)

        if not targets:
            return await reply_text(interaction, "❌ Kein gültiger Kanal ausgewählt.", kind="error", ephemeral=True)

        unlocked_mentions: list[str] = []

        for ch in targets:
            # Laufende (alte) In-Memory-Tasks abbrechen (zur Sicherheit)
            if ch.id in lock_tasks:
                try:
                    lock_tasks[ch.id].cancel()
                except Exception:
                    pass
                lock_tasks.pop(ch.id, None)

            # Sofort entsperren (Rechte)
            await self._apply_unlock(ch)

            # Persistenten Job auf done stellen
            await execute(
                "UPDATE public.lock_jobs SET status='done' WHERE guild_id=$1 AND channel_id=$2",
                ch.guild.id, ch.id
            )

            # Meldung im Kanal
            cfg = await get_guild_cfg(interaction.guild.id)
            tmpl = (cfg.get("templates") or {}).get("unlock", "🔓 Kanal {channel} entsperrt.")
            txt_de = tmpl.format(channel=ch.mention)
            txt = await translate_text_for_guild(interaction.guild.id, txt_de)
            await send_embed(ch, make_embed(title="🔓 Unlock", description=txt, kind="success"))

            unlocked_mentions.append(ch.mention)

        # Zusammenfassung an den Nutzer
        return await reply_text(
            interaction,
            f"✅ Entsperrt: {', '.join(unlocked_mentions)}",
            kind="success",
            ephemeral=True,
        )

    # ---------------------------- Scheduler-Loop ----------------------------

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def scan_lock_jobs(self):
        """
        • pending & fällig -> running setzen, Lock anwenden, ends_at setzen
        • running & abgelaufen -> Unlock + done
        """
        now = datetime.now(timezone.utc)

        # 1) fällige pending-Jobs starten
        rows = await fetch(
            """
            SELECT guild_id, channel_id, run_at, duration_minutes
            FROM public.lock_jobs
            WHERE status='pending' AND run_at <= now()
            """
        )
        for r in rows:
            gid = int(r["guild_id"]); cid = int(r["channel_id"])
            guild = self.bot.get_guild(gid)
            if not guild:
                await execute("UPDATE public.lock_jobs SET status='cancelled' WHERE guild_id=$1 AND channel_id=$2", gid, cid)
                continue
            ch = guild.get_channel(cid)
            if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
                await execute("UPDATE public.lock_jobs SET status='cancelled' WHERE guild_id=$1 AND channel_id=$2", gid, cid)
                continue

            duration = int(r["duration_minutes"])
            started_at = now
            ends_at = started_at + timedelta(minutes=duration)

            # running & Zeiten setzen
            await execute(
                """
                UPDATE public.lock_jobs
                SET status='running', started_at=$3, ends_at=$4
                WHERE guild_id=$1 AND channel_id=$2
                """,
                gid, cid, started_at, ends_at
            )

            await self._apply_lock(ch)
            await self._notify_locked(ch, gid, display_time="jetzt", duration=duration)

        # 2) laufende Jobs mit abgelaufener Endzeit entsperren
        running = await fetch(
            """
            SELECT guild_id, channel_id, ends_at
            FROM public.lock_jobs
            WHERE status='running'
            """
        )
        for r in running:
            gid = int(r["guild_id"]); cid = int(r["channel_id"])
            ends_at = r["ends_at"]
            if not ends_at:
                continue
            if now < ends_at:
                continue

            guild = self.bot.get_guild(gid)
            if not guild:
                await execute("UPDATE public.lock_jobs SET status='cancelled' WHERE guild_id=$1 AND channel_id=$2", gid, cid)
                continue
            ch = guild.get_channel(cid)
            if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
                await execute("UPDATE public.lock_jobs SET status='cancelled' WHERE guild_id=$1 AND channel_id=$2", gid, cid)
                continue

            await self._apply_unlock(ch)
            await self._notify_unlocked(ch, gid)
            await execute(
                "UPDATE public.lock_jobs SET status='done' WHERE guild_id=$1 AND channel_id=$2",
                gid, cid
            )

    @scan_lock_jobs.before_loop
    async def _before_scan(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))