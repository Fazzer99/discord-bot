# bot/cogs/moderation.py
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..utils.checks import require_manage_channels
from ..utils.replies import reply_text, make_embed
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
        delay = max(0, (run_at_utc - now_utc).total_seconds())

        # Templates
        tmpl_lock = (cfg.get("templates") or {}).get(
            "lock",
            "🔒 Kanal {channel} gesperrt um {time} für {duration} Minuten 🚫"
        )
        display_time = local_run.strftime("%H:%M")

        scheduled_mentions: list[str] = []

        for ch in sel:
            # Vorherige Task für diesen Kanal abbrechen
            if ch.id in lock_tasks:
                try:
                    lock_tasks[ch.id].cancel()
                except Exception:
                    pass
                lock_tasks.pop(ch.id, None)

            everyone = interaction.guild.default_role

            priv_over = ch.overwrites_for(everyone)
            is_priv = (priv_over.view_channel is False)
            private_roles: list[discord.Role] = []
            if is_priv:
                for role_obj, over in ch.overwrites.items():
                    if isinstance(role_obj, discord.Role) and over.view_channel:
                        private_roles.append(role_obj)

            async def _do_lock(target_ch: discord.TextChannel | discord.VoiceChannel, wait: float, dur_min: int):
                await asyncio.sleep(wait)

                # Rechte setzen (lock)
                if isinstance(target_ch, discord.TextChannel):
                    if is_priv:
                        for r in private_roles:
                            await target_ch.set_permissions(r, send_messages=False, view_channel=True)
                    else:
                        await target_ch.set_permissions(everyone, send_messages=False)
                else:
                    if is_priv:
                        for r in private_roles:
                            await target_ch.set_permissions(r, connect=False, speak=False, view_channel=True)
                    else:
                        await target_ch.set_permissions(everyone, connect=False, speak=False)
                    # ggf. alle rausschmeißen
                    for m in list(target_ch.members):
                        try:
                            await m.move_to(None)
                        except Exception:
                            pass

                # Info im Kanal (Zeit in lokaler Zeit anzeigen) — als Embed
                msg_de = tmpl_lock.format(channel=target_ch.mention, time=display_time, duration=dur_min)
                msg = await translate_text_for_guild(interaction.guild.id, msg_de)
                emb = make_embed(title="🔒 Lock aktiviert", description=msg, kind="warning")
                await target_ch.send(embed=emb)

                # Warten und wieder entsperren
                await asyncio.sleep(dur_min * 60)
                if isinstance(target_ch, discord.TextChannel):
                    if is_priv:
                        for r in private_roles:
                            await target_ch.set_permissions(r, send_messages=None, view_channel=True)
                    else:
                        await target_ch.set_permissions(everyone, send_messages=None)
                else:
                    if is_priv:
                        for r in private_roles:
                            await target_ch.set_permissions(r, connect=None, speak=None, view_channel=True)
                    else:
                        await target_ch.set_permissions(everyone, connect=None, speak=None)

                text_unlocked = await translate_text_for_guild(
                    interaction.guild.id,
                    "🔓 Kanal automatisch entsperrt – viel Spaß! 🎉"
                )
                emb_un = make_embed(title="🔓 Unlock", description=text_unlocked, kind="success")
                await target_ch.send(embed=emb_un)
                lock_tasks.pop(target_ch.id, None)

            task = self.bot.loop.create_task(_do_lock(ch, delay, duration))
            lock_tasks[ch.id] = task
            scheduled_mentions.append(ch.mention)

        # Bestätigung in lokaler Zeit (Embed via reply_text)
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
            # Laufende Task abbrechen
            if ch.id in lock_tasks:
                try:
                    lock_tasks[ch.id].cancel()
                except Exception:
                    pass
                lock_tasks.pop(ch.id, None)

            everyone = ch.guild.default_role
            is_priv = ch.overwrites_for(everyone).view_channel is False
            private_roles: list[discord.Role] = []
            if is_priv:
                for role_obj, over in ch.overwrites.items():
                    if isinstance(role_obj, discord.Role) and over.view_channel:
                        private_roles.append(role_obj)

            # Rechte zurücksetzen (unlock)
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

            # Meldung im Kanal als Embed
            cfg = await get_guild_cfg(interaction.guild.id)
            tmpl = (cfg.get("templates") or {}).get("unlock", "🔓 Kanal {channel} entsperrt.")
            txt_de = tmpl.format(channel=ch.mention)
            txt = await translate_text_for_guild(interaction.guild.id, txt_de)
            await ch.send(embed=make_embed(title="🔓 Unlock", description=txt, kind="success"))

            unlocked_mentions.append(ch.mention)

        # Zusammenfassung an den Nutzer
        return await reply_text(
            interaction,
            f"✅ Entsperrt: {', '.join(unlocked_mentions)}",
            kind="success",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))