# bot/cogs/cleanup.py
from __future__ import annotations
import asyncio
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands
from typing import List

from ..utils.replies import reply_text
from ..utils.checks import require_manage_messages

cleanup_tasks: dict[int, asyncio.Task] = {}

def _compute_pre_notify(interval: float) -> float | None:
    if interval >= 3600:
        return interval - 3600
    if interval >= 300:
        return interval - 300
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

class CleanupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="cleanup",
        description="Starte wiederkehrende automatische Nachrichtenlöschung in Kanälen."
    )
    @require_manage_messages()
    @app_commands.describe(
        channels="Textkanäle, in denen gelöscht werden soll",
        days="Intervall in Tagen",
        minutes="Zusätzliches Intervall in Minuten"
    )
    async def cleanup(
        self,
        interaction: discord.Interaction,
        channels: List[discord.TextChannel],
        days: int,
        minutes: int
    ):
        if not channels:
            return await reply_text(interaction, "❌ Bitte mindestens einen Kanal angeben.", kind="error")

        interval = days * 86400 + minutes * 60
        if interval <= 0:
            return await reply_text(interaction, "❌ Ungültiges Intervall.", kind="error")

        await reply_text(
            interaction,
            f"🗑️ Nachrichten in {', '.join(ch.mention for ch in channels)} werden alle {days} Tage und {minutes} Minuten gelöscht.",
            kind="info"
        )

        for ch in channels:
            if ch.id in cleanup_tasks:
                cleanup_tasks[ch.id].cancel()

            async def _loop_cleanup(channel: discord.TextChannel, interval_s: float):
                # Initial: alles löschen
                await _purge_all(channel)
                try:
                    await reply_text(channel, "🗑️ Alle Nachrichten wurden automatisch gelöscht.", kind="success")
                except discord.Forbidden:
                    pass

                pre = _compute_pre_notify(interval_s)
                while True:
                    if pre is not None:
                        await asyncio.sleep(pre)
                        wm = (interval_s - pre) / 60
                        text = (f"in {int(wm//60)} Stunde(n)" if wm >= 60 else f"in {int(wm)} Minute(n)")
                        try:
                            await reply_text(channel, f"⚠️ Achtung: {text}, dann werden alle Nachrichten gelöscht.", kind="warning")
                        except discord.Forbidden:
                            pass
                        await asyncio.sleep(interval_s - pre)
                    else:
                        await asyncio.sleep(interval_s)

                    await _purge_all(channel)
                    try:
                        await reply_text(channel, "🗑️ Alle Nachrichten wurden automatisch gelöscht.", kind="success")
                    except discord.Forbidden:
                        pass

            task = self.bot.loop.create_task(_loop_cleanup(ch, interval))
            cleanup_tasks[ch.id] = task

    @app_commands.command(
        name="cleanup_stop",
        description="Stoppe die automatische Nachrichtenlöschung in Kanälen."
    )
    @require_manage_messages()
    @app_commands.describe(
        channels="Textkanäle, in denen das Löschen gestoppt werden soll"
    )
    async def cleanup_stop(
        self,
        interaction: discord.Interaction,
        channels: List[discord.TextChannel]
    ):
        if not channels:
            return await reply_text(interaction, "❌ Bitte mindestens einen Kanal angeben.", kind="error")

        for ch in channels:
            task = cleanup_tasks.pop(ch.id, None)
            if task:
                task.cancel()
                await reply_text(interaction, f"🛑 Automatische Löschung in {ch.mention} gestoppt.", kind="success")
            else:
                await reply_text(interaction, f"ℹ️ Keine laufende Löschung in {ch.mention} gefunden.", kind="warning")

async def setup(bot: commands.Bot):
    await bot.add_cog(CleanupCog(bot))