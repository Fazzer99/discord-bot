# bot/cogs/cleanup.py
from __future__ import annotations
import asyncio
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

from ..utils.checks import require_manage_messages
from ..utils.replies import reply_text
from ..services.translation import translate_text_for_guild
from ..utils.checks import GuildLangGuard

cleanup_tasks: dict[int, asyncio.Task] = {}

def _compute_pre_notify(interval: float) -> float | None:
    if interval >= 3600: return interval - 3600
    if interval >= 300:  return interval - 300
    return None

def age_seconds(msg: discord.Message) -> float:
    now = datetime.now(tz=msg.created_at.tzinfo)
    return (now - msg.created_at).total_seconds()

async def _purge_all(channel: discord.TextChannel):
    cutoff = 14 * 24 * 3600
    while True:
        msgs = [m async for m in channel.history(limit=100)]
        if not msgs:
            break
        to_bulk = [m for m in msgs if age_seconds(m) < cutoff]
        for i in range(0, len(to_bulk), 100):
            await channel.delete_messages(to_bulk[i:i+100])
            await asyncio.sleep(3)
        old = [m for m in msgs if age_seconds(m) >= cutoff]
        for m in old:
            await m.delete()
            await asyncio.sleep(1)

class CleanupCog(GuildLangGuard, commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
        interval = days * 86400 + minutes * 60
        if interval <= 0:
            return await reply_text(interaction, "❌ Ungültiges Intervall.", kind="error")

        # vorigen Task stoppen
        if channel.id in cleanup_tasks:
            cleanup_tasks[channel.id].cancel()

        async def _loop_cleanup(ch: discord.TextChannel, interval_s: float):
            await _purge_all(ch)
            try:
                msg = await translate_text_for_guild(ch.guild.id, "🗑️ Alle Nachrichten wurden automatisch gelöscht.")
                await ch.send(msg)
            except discord.Forbidden:
                pass

            pre = _compute_pre_notify(interval_s)
            while True:
                if pre is not None:
                    await asyncio.sleep(pre)
                    wm = (interval_s - pre) / 60
                    text = (f"in {int(wm//60)} Stunde(n)" if wm >= 60 else f"in {int(wm)} Minute(n)")
                    warn = await translate_text_for_guild(ch.guild.id, f"⚠️ Achtung: {text}, dann werden alle Nachrichten gelöscht.")
                    await ch.send(warn)
                    await asyncio.sleep(interval_s - pre)
                else:
                    await asyncio.sleep(interval_s)

                await _purge_all(ch)
                try:
                    msg = await translate_text_for_guild(ch.guild.id, "🗑️ Alle Nachrichten wurden automatisch gelöscht.")
                    await ch.send(msg)
                except discord.Forbidden:
                    pass

        task = self.bot.loop.create_task(_loop_cleanup(channel, interval))
        cleanup_tasks[channel.id] = task

        return await reply_text(
            interaction,
            f"🗑️ Nachrichten in {channel.mention} werden alle {days} Tage und {minutes} Minuten gelöscht.",
            kind="info"
        )

    @app_commands.command(
        name="cleanup_stop",
        description="Stoppt die automatische Löschung."
    )
    @require_manage_messages()
    @app_commands.describe(channel="Textkanal")
    async def cleanup_stop(self, interaction: discord.Interaction, channel: discord.TextChannel):
        task = cleanup_tasks.pop(channel.id, None)
        if task:
            task.cancel()
            return await reply_text(interaction, f"🛑 Automatische Löschung in {channel.mention} gestoppt.", kind="success")
        else:
            return await reply_text(interaction, f"ℹ️ Keine laufende Löschung in {channel.mention} gefunden.", kind="info")

async def setup(bot: commands.Bot):
    await bot.add_cog(CleanupCog(bot))