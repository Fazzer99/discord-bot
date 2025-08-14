# bot/cogs/moderation.py
from __future__ import annotations
import asyncio
from datetime import timedelta
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
        if duration <= 0:
            return await reply_text(interaction, "❌ Ungültige Dauer.", kind="error")

        # Zeit parsen
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

        # Vorherige Task abbrechen
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()

        priv_over = channel.overwrites_for(everyone)
        is_priv = (priv_over.view_channel is False)
        private_roles: list[discord.Role] = []
        if is_priv:
            for role_obj, over in channel.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

        async def _do_lock(ch, wait, dur):
            await asyncio.sleep(wait)
            # Rechte setzen
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
                for m in ch.members:
                    try:
                        await m.move_to(None)
                    except:  # noqa: E722
                        pass

            # Info im Kanal
            msg_de = tmpl.format(channel=ch.mention, time=start_time, duration=dur)
            msg = await translate_text_for_guild(interaction.guild.id, msg_de)
            await ch.send(msg)

            # Timer & Unlock
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

        return await reply_text(
            interaction,
            f"⏰ {channel.mention} wird um {start_time} Uhr für {duration} Minuten gesperrt.",
            kind="info"
        )

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
        everyone = interaction.guild.default_role

        # laufende Task abbrechen
        if channel.id in lock_tasks:
            lock_tasks[channel.id].cancel()
            lock_tasks.pop(channel.id, None)

        is_priv = channel.overwrites_for(everyone).view_channel is False
        private_roles: list[discord.Role] = []
        if is_priv:
            for role_obj, over in channel.overwrites.items():
                if isinstance(role_obj, discord.Role) and over.view_channel:
                    private_roles.append(role_obj)

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