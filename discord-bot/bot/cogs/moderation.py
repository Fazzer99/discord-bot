from __future__ import annotations
import asyncio
from datetime import timedelta
import discord
from discord import app_commands
from discord.ext import commands
from typing import List
from ..utils.checks import require_manage_channels
from ..utils.replies import reply_text
from ..services.guild_config import get_guild_cfg

lock_tasks: dict[int, asyncio.Task] = {}

class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="lock", description="Sperrt Kanäle für X Minuten ab Zeitpunkt HH:MM")
    @require_manage_channels()
    @app_commands.describe(
        channels="Einer oder mehrere Kanäle (Text oder Voice)",
        start_time="Startzeit im Format HH:MM (Server-Zeit)",
        duration="Dauer in Minuten"
    )
    async def lock(
        self,
        interaction: discord.Interaction,
        channels: List[discord.abc.GuildChannel],
        start_time: str,
        duration: int
    ):
        if not channels:
            return await reply_text(interaction, "❌ Bitte mindestens einen Kanal angeben.", kind="error")

        try:
            hour, minute = map(int, start_time.split(":"))
        except ValueError:
            return await reply_text(interaction, "❌ Ungültiges Format. Bitte `HH:MM` im 24h-Format.", kind="error")

        now = discord.utils.utcnow()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        everyone = interaction.guild.default_role

        cfg = await get_guild_cfg(interaction.guild.id)
        tmpl = cfg.get("templates", {}).get(
            "lock",
            "🔒 Kanal {channel} gesperrt um {time} für {duration} Minuten 🚫"
        )

        for ch in channels:
            if ch.id in lock_tasks:
                lock_tasks[ch.id].cancel()

            priv_over = ch.overwrites_for(everyone)
            is_priv = (priv_over.view_channel is False)
            private_roles = []
            if is_priv:
                for role_obj, over in ch.overwrites.items():
                    if isinstance(role_obj, discord.Role) and over.view_channel:
                        private_roles.append(role_obj)

            async def _do_lock(channel, wait, dur):
                await asyncio.sleep(wait)
                # Sperre setzen
                if isinstance(channel, discord.TextChannel):
                    if is_priv:
                        for r in private_roles:
                            await channel.set_permissions(r, send_messages=False, view_channel=True)
                    else:
                        await channel.set_permissions(everyone, send_messages=False)
                else:
                    if is_priv:
                        for r in private_roles:
                            await channel.set_permissions(r, connect=False, speak=False, view_channel=True)
                    else:
                        await channel.set_permissions(everyone, connect=False, speak=False)
                    for m in channel.members:
                        try:
                            await m.move_to(None)
                        except:
                            pass

                # Meldung (rot = „gesperrt“)
                msg_de = tmpl.format(channel=channel.mention, time=start_time, duration=dur)
                await reply_text(channel, msg_de, kind="error")

                # Timer und Entsperren
                await asyncio.sleep(dur * 60)
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

                await reply_text(channel, "🔓 Kanal automatisch entsperrt – viel Spaß! 🎉", kind="success")
                lock_tasks.pop(channel.id, None)

            task = self.bot.loop.create_task(_do_lock(ch, delay, duration))
            lock_tasks[ch.id] = task
            await reply_text(
                interaction,
                f"⏰ {ch.mention} wird um {start_time} Uhr für {duration} Minuten gesperrt.",
                kind="info"
            )

    @app_commands.command(name="unlock", description="Hebt die Sperre sofort auf")
    @require_manage_channels()
    @app_commands.describe(channels="Einer oder mehrere Kanäle (Text oder Voice)")
    async def unlock(self, interaction: discord.Interaction, channels: List[discord.abc.GuildChannel]):
        if not channels:
            return await reply_text(interaction, "❌ Bitte mindestens einen Kanal angeben.", kind="error")

        everyone = interaction.guild.default_role

        for ch in channels:
            if ch.id in lock_tasks:
                lock_tasks[ch.id].cancel()
                lock_tasks.pop(ch.id, None)

            is_priv = ch.overwrites_for(everyone).view_channel is False
            private_roles = []
            if is_priv:
                for role_obj, over in ch.overwrites.items():
                    if isinstance(role_obj, discord.Role) and over.view_channel:
                        private_roles.append(role_obj)

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

            await reply_text(ch, "🔓 Kanal entsperrt.", kind="success")

        await reply_text(interaction, "🔓 Alle angegebenen Kanäle wurden entsperrt.", kind="success")

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))