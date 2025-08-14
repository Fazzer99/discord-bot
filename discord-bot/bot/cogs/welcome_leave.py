# bot/cogs/welcome_leave.py
from __future__ import annotations
import discord
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo

from ..services.guild_config import get_guild_cfg
from ..utils.replies import reply_text

class WelcomeLeaveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        cfg = await get_guild_cfg(after.guild.id)
        role_id    = cfg.get("welcome_role")
        channel_id = cfg.get("welcome_channel")
        tmpl       = cfg.get("templates", {}).get("welcome")

        if not (role_id and channel_id and tmpl):
            return

        had_before = any(r.id == role_id for r in before.roles)
        has_now    = any(r.id == role_id for r in after.roles)
        if had_before or not has_now:
            return

        channel = after.guild.get_channel(channel_id)
        if channel is None:
            return

        text_de = tmpl.format(member=after.mention, guild=after.guild.name)
        await reply_text(channel, text_de, kind="success")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        cfg = await get_guild_cfg(member.guild.id)
        leave_chan = cfg.get("leave_channel")
        tmpl       = cfg.get("templates", {}).get("leave")
        if not (leave_chan and tmpl):
            return

        # Kick- und Ban-Check
        now = datetime.now(tz=ZoneInfo("Europe/Berlin"))
        kicked = False
        try:
            async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
                if entry.target.id == member.id and (now - entry.created_at).total_seconds() < 5:
                    kicked = True
                break
        except discord.Forbidden:
            pass
        if kicked:
            return
        try:
            await member.guild.fetch_ban(member)
            return
        except (discord.NotFound, discord.Forbidden):
            pass

        channel = member.guild.get_channel(leave_chan)
        if channel is None:
            return

        text_de = tmpl.format(member=member.mention, guild=member.guild.name)
        await reply_text(channel, text_de, kind="error")

async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeLeaveCog(bot))