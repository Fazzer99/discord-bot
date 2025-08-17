# bot/cogs/moderation.py
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands

from ..utils.checks import require_manage_channels
from ..utils.replies import reply_text
from ..services.guild_config import get_guild_cfg
from ..services.translation import translate_text_for_guild

lock_tasks: dict[int, asyncio.Task] = {}

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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

    # -------------------------------- lock ---------------------------------

    @app_commands.command(
        name="lock",
        description="Sperrt einen Kanal ab Zeitpunkt HH:MM für X Minuten."
    )
    @require_manage_channels()
    @app_commands.describe(
        channel="Text- oder Voice-Kanal",
        start_time="Startzeit im Format HH:MM (Server-Zeit)",
        duration="Dauer in Minuten"
    )
    async def lock(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel,
        start_time: str,
        duration: int
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if duration <= 0:
            return await reply_text(interaction, "❌ Ungültige Dauer.", kind="error")

        # HH:MM parsen (lokale Serverzeit)
        try:
            hour, minute = [int(x) for x in start_time.split(":")]
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except Exception:
            return await reply_text(
                interaction, "❌ Ungültiges Format. Bitte `HH:MM` im 24h-Format.",
                kind="error"
            )

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
        delay = max(0, (run_at_utc - now_utc).total_seconds())

        # Bestimme @everyone + Privat-Infos
        everyone = interaction.guild.default_role

        tmpl = cfg.get("templates", {}).get(
            "lock",
            "🔒 Kanal {channel} gesperrt um {time} für {duration} Minuten 🚫"
        )

        # Vorherige Task für diesen Kanal abbrechen
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()

        priv_over = channel.overwrites_for(everyone)
        is_priv = (priv_over.view_channel is False)
        private_roles: list[discord.Role] = []
        if is_priv:
            for role_obj, over in channel.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

        display_time = local_run.strftime("%H:%M")

        async def _do_lock(ch, wait, dur):
            await asyncio.sleep(wait)

            # Rechte setzen (lock)
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
                # ggf. alle rausschmeißen
                for m in ch.members:
                    try:
                        await m.move_to(None)
                    except Exception:
                        pass

            # Info im Kanal (Zeit in lokaler Zeit anzeigen)
            msg_de = tmpl.format(channel=ch.mention, time=display_time, duration=dur)
            msg = await translate_text_for_guild(interaction.guild.id, msg_de)
            await ch.send(msg)

            # Warten und wieder entsperren
            await asyncio.sleep(dur * 60)
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

            text_unlocked = await translate_text_for_guild(
                interaction.guild.id,
                "🔓 Kanal automatisch entsperrt – viel Spaß! 🎉"
            )
            await ch.send(text_unlocked)
            lock_tasks.pop(ch.id, None)

        task = self.bot.loop.create_task(_do_lock(channel, delay, duration))
        lock_tasks[channel.id] = task

        # Bestätigung in lokaler Zeit
        return await reply_text(
            interaction,
            f"⏰ {channel.mention} wird um **{display_time}** Uhr für **{duration}** Minuten gesperrt.",
            kind="info"
        )

    # -------------------------------- unlock -------------------------------

    @app_commands.command(
        name="unlock",
        description="Hebt die Sperre eines Kanals sofort auf."
    )
    @require_manage_channels()
    @app_commands.describe(channel="Text- oder Voice-Kanal")
    async def unlock(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        everyone = interaction.guild.default_role

        # Laufende Task abbrechen
        if channel.id in lock_tasks:
            try:
                lock_tasks[channel.id].cancel()
            except Exception:
                pass
            lock_tasks.pop(channel.id, None)

        is_priv = channel.overwrites_for(everyone).view_channel is False
        private_roles: list[discord.Role] = []
        if is_priv:
            for role_obj, over in channel.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

        # Rechte zurücksetzen (unlock)
        if isinstance(channel, discord.TextChannel):
            if is_priv:
                for r in private_roles:
                    await channel.set_permissions(r, send_messages=None, view_channel=True)
            else:
                await channel.set_permissions(everyone, send_messages=None)
        else:
            if is_priv:
                for r in private_roles:
                    await channel.set_permissions(r, connect=None, speak=None, view_channel=True)
            else:
                await channel.set_permissions(everyone, connect=None, speak=None)

        cfg = await get_guild_cfg(interaction.guild.id)
        tmpl = cfg.get("templates", {}).get("unlock", "🔓 Kanal {channel} entsperrt.")
        txt_de = tmpl.format(channel=channel.mention)
        txt = await translate_text_for_guild(interaction.guild.id, txt_de)
        await channel.send(txt)

        return await reply_text(interaction, "✅ Entsperrt.", kind="success")


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))