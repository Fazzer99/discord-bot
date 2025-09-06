# bot/cogs/cleanup.py
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..utils.checks import require_manage_messages
from ..utils.replies import reply_text, tracked_send  # <-- tracked_send hinzugefügt
from ..services.translation import translate_text_for_guild
from ..db import fetch, execute  # <-- DB für Regeln

cleanup_tasks: dict[int, asyncio.Task] = {}

def _compute_pre_notify(interval: float) -> float | None:
    if interval >= 3600: return interval - 3600
    if interval >= 300:  return interval - 300
    return None

def age_seconds(msg: discord.Message) -> float:
    now = datetime.now(tz=msg.created_at.tzinfo)
    return (now - msg.created_at).total_seconds()

async def _purge_all(channel: discord.TextChannel):
    cut14 = 14 * 24 * 3600
    while True:
        msgs = [m async for m in channel.history(limit=100)]
        if not msgs:
            break
        to_bulk = [m for m in msgs if age_seconds(m) < cut14]
        for i in range(0, len(to_bulk), 100):
            try:
                await channel.delete_messages(to_bulk[i:i+100])
            except Exception:
                pass
            await asyncio.sleep(3)
        old = [m for m in msgs if age_seconds(m) >= cut14]
        for m in old:
            try:
                await m.delete()
            except Exception:
                pass
            await asyncio.sleep(1)

class CleanupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # integrierter Scheduler
        self.scan_cleanup_rules.start()

    def cog_unload(self):
        try:
            self.scan_cleanup_rules.cancel()
        except Exception:
            pass

    @app_commands.command(
        name="cleanup",
        description="Löscht Nachrichten eines Kanals in einem wiederkehrenden Intervall."
    )
    @require_manage_messages()
    @app_commands.describe(
        channel="Textkanal",
        days="Tage zwischen Löschläufen",
        minutes="Minuten zusätzlich"
    )
    async def cleanup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        days: int,
        minutes: int
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        interval = days * 86400 + minutes * 60
        if interval <= 0:
            return await reply_text(interaction, "❌ Ungültiges Intervall.", kind="error", ephemeral=True)

        # vorigen In-Memory-Task stoppen
        if channel.id in cleanup_tasks:
            cleanup_tasks[channel.id].cancel()
            cleanup_tasks.pop(channel.id, None)

        # Regel persistieren (next_run_at = jetzt + Intervall)
        next_run = datetime.now(timezone.utc) + timedelta(seconds=interval)
        await execute(
            """
            INSERT INTO public.cleanup_rules
                (guild_id, channel_id, enabled, interval_days, interval_minutes,
                 max_message_age_minutes, keep_pinned, next_run_at)
            VALUES ($1, $2, true, $3, $4, NULL, true, $5)
            ON CONFLICT (guild_id, channel_id)
            DO UPDATE SET enabled=true,
                          interval_days=EXCLUDED.interval_days,
                          interval_minutes=EXCLUDED.interval_minutes,
                          next_run_at=EXCLUDED.next_run_at
            """,
            interaction.guild.id, channel.id, int(days), int(minutes), next_run
        )

        # Optionaler In-Memory-Loop als Fallback (nicht nötig, aber harmless)
        async def _loop_cleanup(ch: discord.TextChannel, interval_s: float):
            await _purge_all(ch)
            try:
                msg = await translate_text_for_guild(ch.guild.id, "🗑️ Alle Nachrichten wurden automatisch gelöscht.")
                await tracked_send(ch, content=msg, guild_id=ch.guild.id)
            except discord.Forbidden:
                pass

            pre = _compute_pre_notify(interval_s)
            while True:
                if pre is not None:
                    await asyncio.sleep(pre)
                    wm = (interval_s - pre) / 60
                    text = (f"in {int(wm//60)} Stunde(n)" if wm >= 60 else f"in {int(wm)} Minute(n)")
                    warn = await translate_text_for_guild(ch.guild.id, f"⚠️ Achtung: {text}, dann werden alle Nachrichten gelöscht.")
                    await tracked_send(ch, content=warn, guild_id=ch.guild.id)
                    await asyncio.sleep(interval_s - pre)
                else:
                    await asyncio.sleep(interval_s)

                await _purge_all(ch)
                try:
                    msg = await translate_text_for_guild(ch.guild.id, "🗑️ Alle Nachrichten wurden automatisch gelöscht.")
                    await tracked_send(ch, content=msg, guild_id=ch.guild.id)
                except discord.Forbidden:
                    pass

        task = self.bot.loop.create_task(_loop_cleanup(channel, interval))
        cleanup_tasks[channel.id] = task

        return await reply_text(
            interaction,
            f"🗑️ Nachrichten in {channel.mention} werden alle {days} Tage und {minutes} Minuten gelöscht.",
            kind="info",
            ephemeral=True
        )

    @app_commands.command(
        name="cleanup_stop",
        description="Stoppt die automatische Löschung."
    )
    @require_manage_messages()
    @app_commands.describe(channel="Textkanal")
    async def cleanup_stop(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # In-Memory stoppen
        task = cleanup_tasks.pop(channel.id, None)
        if task:
            task.cancel()

        # Persistente Regel deaktivieren
        await execute(
            "UPDATE public.cleanup_rules SET enabled=false WHERE guild_id=$1 AND channel_id=$2",
            interaction.guild.id, channel.id
        )

        return await reply_text(interaction, f"🛑 Automatische Löschung in {channel.mention} gestoppt.", kind="success", ephemeral=True)

    # ---------------------------- Scheduler-Loop ----------------------------

    @tasks.loop(seconds=30)
    async def scan_cleanup_rules(self):
        """
        Führt fällige Cleanups aus (enabled & next_run_at <= now),
        setzt last_run_at und berechnet next_run_at neu.
        """
        now = datetime.now(timezone.utc)
        rows = await fetch(
            """
            SELECT guild_id, channel_id, enabled, interval_days, interval_minutes,
                   max_message_age_minutes, keep_pinned, next_run_at
            FROM public.cleanup_rules
            WHERE enabled = true AND next_run_at <= now()
            """
        )
        for r in rows:
            gid = int(r["guild_id"]); cid = int(r["channel_id"])
            guild = self.bot.get_guild(gid)
            if not guild:
                await execute("UPDATE public.cleanup_rules SET enabled=false WHERE guild_id=$1 AND channel_id=$2", gid, cid)
                continue

            ch = guild.get_channel(cid)
            if not isinstance(ch, discord.TextChannel):
                await execute("UPDATE public.cleanup_rules SET enabled=false WHERE guild_id=$1 AND channel_id=$2", gid, cid)
                continue

            # Cleanup ausführen
            try:
                await _purge_all(ch)
                msg = await translate_text_for_guild(gid, "🗑️ Alle Nachrichten wurden automatisch gelöscht.")
                await tracked_send(ch, content=msg, guild_id=gid)
            except Exception:
                pass

            # Nächsten Lauf planen
            dd = max(0, int(r["interval_days"]))
            mm = max(0, int(r["interval_minutes"]))
            delta = timedelta(days=dd, minutes=mm)
            next_run = now + (delta if delta.total_seconds() > 0 else timedelta(days=1))

            await execute(
                """
                UPDATE public.cleanup_rules
                SET last_run_at = now(),
                    next_run_at = $3
                WHERE guild_id=$1 AND channel_id=$2
                """,
                gid, cid, next_run
            )

    @scan_cleanup_rules.before_loop
    async def _before_cleanup_scan(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(CleanupCog(bot))