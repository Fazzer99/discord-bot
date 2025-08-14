# bot/cogs/admin.py
from __future__ import annotations
import asyncio
import json
import discord
from discord import app_commands
from discord.ext import commands

from ..utils.checks import require_manage_guild
from ..utils.replies import reply_text
from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..db import execute, fetchrow


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # /setlang
    @app_commands.command(name="setlang", description="Setzt die Bot-Sprache für diesen Server (de|en)")
    @require_manage_guild()
    @app_commands.describe(lang="Zulässig: de | en")
    async def setlang(self, interaction: discord.Interaction, lang: str):
        lang = (lang or "").strip().lower()
        if lang not in ("de", "en"):
            return await reply_text(interaction, "❌ Ungültige Sprache. Erlaubt: `de` oder `en`.", kind="error")

        await update_guild_cfg(interaction.guild.id, lang=lang)
        if lang == "de":
            msg = "✅ Sprache gesetzt auf **Deutsch**."
        else:
            msg = "✅ Language set to **English**."
        return await reply_text(interaction, msg, kind="success")

    # Globaler Slash-Check
    @staticmethod
    async def ensure_lang_set(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            return True
        cmd_name = interaction.command.name if interaction.command else ""
        if cmd_name == "setlang":
            return True
        cfg = await get_guild_cfg(interaction.guild.id)
        lang = (cfg.get("lang") or "").lower()
        if lang in ("de", "en"):
            return True
        await reply_text(
            interaction,
            "🌐 Bitte zuerst die Sprache wählen mit `/setlang de` oder `/setlang en`.",
            kind="warning"
        )
        raise app_commands.CheckFailure("Guild language not set")

    # /setup
    @app_commands.command(name="setup", description="Interaktives Setup für Module (welcome, leave, vc_override, autorole, vc_track)")
    @require_manage_guild()
    @app_commands.describe(module="welcome | leave | vc_override | autorole | vc_track")
    async def setup(self, interaction: discord.Interaction, module: str):
        module = module.lower()
        valid = ("welcome", "leave", "vc_override", "autorole", "vc_track")
        if module not in valid:
            return await reply_text(interaction, "❌ Unbekanntes Modul.", kind="error")

        await interaction.response.defer()
        author = interaction.user
        channel = interaction.channel

        def check_msg(msg: discord.Message, cond) -> bool:
            return msg.author == author and msg.channel == channel and cond(msg)

        # vc_override
        if module == "vc_override":
            await reply_text(channel, "❓ Bitte erwähne den Sprachkanal.", kind="info")
            try:
                msg_chan = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen.", kind="warning")
            vc_channel = msg_chan.channel_mentions[0]

            row = await fetchrow("SELECT 1 FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                                 interaction.guild.id, vc_channel.id)
            if row:
                return await reply_text(channel, f"❌ {vc_channel.mention} ist bereits **vc_track**.", kind="error")

            await reply_text(channel, "❓ Override-Rollen erwähnen.", kind="info")
            msg_o = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            override_ids = [r.id for r in msg_o.role_mentions]

            await reply_text(channel, "❓ Ziel-Rollen erwähnen.", kind="info")
            msg_t = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            target_ids = [r.id for r in msg_t.role_mentions]

            await reply_text(channel, "❓ VC-Log-Kanal erwähnen.", kind="info")
            msg_log = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
            vc_log_channel = msg_log.channel_mentions[0]
            await update_guild_cfg(interaction.guild.id, vc_log_channel=vc_log_channel.id)

            await execute(
                """
                INSERT INTO vc_overrides (guild_id, channel_id, override_roles, target_roles)
                VALUES ($1, $2, $3::jsonb, $4::jsonb)
                ON CONFLICT (guild_id, channel_id) DO UPDATE
                  SET override_roles = EXCLUDED.override_roles,
                      target_roles   = EXCLUDED.target_roles
                """,
                interaction.guild.id, vc_channel.id,
                json.dumps(override_ids), json.dumps(target_ids)
            )
            return await reply_text(channel, f"🎉 **vc_override** für {vc_channel.mention} gespeichert.", kind="success")

        # vc_track
        if module == "vc_track":
            await reply_text(channel, "❓ Bitte Sprachkanal erwähnen.", kind="info")
            msg_chan = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
            vc_channel = msg_chan.channel_mentions[0]

            row = await fetchrow("SELECT 1 FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2",
                                 interaction.guild.id, vc_channel.id)
            if row:
                return await reply_text(channel, f"❌ {vc_channel.mention} ist bereits **vc_override**.", kind="error")

            cfg = await get_guild_cfg(interaction.guild.id)
            if not cfg.get("vc_log_channel"):
                await reply_text(channel, "❓ VC-Log-Kanal erwähnen.", kind="info")
                msg_log = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
                log_ch = msg_log.channel_mentions[0]
                await update_guild_cfg(interaction.guild.id, vc_log_channel=log_ch.id)

            row2 = await fetchrow("SELECT 1 FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                                  interaction.guild.id, vc_channel.id)
            if row2:
                return await reply_text(channel, f"ℹ️ VC-Tracking für {vc_channel.mention} schon aktiv.", kind="info")

            await execute("INSERT INTO vc_tracking (guild_id, channel_id) VALUES ($1, $2)",
                          interaction.guild.id, vc_channel.id)
            return await reply_text(channel, f"🎉 VC-Tracking aktiv für {vc_channel.mention}.", kind="success")

        # autorole
        if module == "autorole":
            await reply_text(channel, "❓ Rolle erwähnen.", kind="info")
            msg_r = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            autorole = msg_r.role_mentions[0]
            await update_guild_cfg(interaction.guild.id, default_role=autorole.id)
            return await reply_text(channel, f"🎉 Autorole: {autorole.mention}.", kind="success")

        # welcome / leave
        await reply_text(channel, "❓ Kanal erwähnen.", kind="info")
        msg = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
        target_channel = msg.channel_mentions[0]
        await update_guild_cfg(interaction.guild.id, **{f"{module}_channel": target_channel.id})

        if module == "welcome":
            await reply_text(channel, "❓ Rolle für Willkommensnachricht erwähnen.", kind="info")
            msgr = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            await update_guild_cfg(interaction.guild.id, welcome_role=msgr.role_mentions[0].id)

        if module in ("welcome", "leave"):
            await reply_text(channel, "✅ Kanal gesetzt. Nachrichtentext eingeben.", kind="info")
            msg2 = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: bool(x.content.strip())), timeout=300)
            cfg = await get_guild_cfg(interaction.guild.id)
            templates = cfg.get("templates") or {}
            if isinstance(templates, str):
                try:
                    templates = json.loads(templates)
                except json.JSONDecodeError:
                    templates = {}
            templates[module] = msg2.content
            await update_guild_cfg(interaction.guild.id, templates=templates)

        return await reply_text(channel, f"🎉 {module}-Setup abgeschlossen!", kind="success")

    # /disable – nur EIN Kanal optional
    @app_commands.command(name="disable", description="Deaktiviert ein Modul und entfernt zugehörige Daten")
    @require_manage_guild()
    @app_commands.describe(
        module="welcome | leave | vc_override | autorole | vc_track",
        channel="Optional: nur für einen bestimmten Kanal (vc_override/vc_track)"
    )
    async def disable(self, interaction: discord.Interaction, module: str, channel: discord.abc.GuildChannel | None = None):
        module = module.lower()
        allowed = ("welcome", "leave", "vc_override", "autorole", "vc_track")
        if module not in allowed:
            return await reply_text(interaction, "❌ Unbekanntes Modul.", kind="error")

        gid = interaction.guild.id

        if module == "autorole":
            await update_guild_cfg(gid, default_role=None)
            return await reply_text(interaction, "🗑️ Autorole deaktiviert.", kind="success")

        if module == "vc_track":
            if channel:
                await execute("DELETE FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_text(interaction, f"🗑️ VC-Tracking entfernt für {channel.mention}.", kind="success")
            await execute("DELETE FROM vc_tracking WHERE guild_id=$1", gid)
            return await reply_text(interaction, "🗑️ VC-Tracking für alle Kanäle entfernt.", kind="success")

        if module in ("welcome", "leave"):
            cfg = await get_guild_cfg(gid)
            fields = {}
            if module == "welcome":
                fields.update(welcome_channel=None, welcome_role=None)
            else:
                fields.update(leave_channel=None)
            templates = cfg.get("templates") or {}
            if isinstance(templates, str):
                try:
                    templates = json.loads(templates)
                except json.JSONDecodeError:
                    templates = {}
            templates.pop(module, None)
            fields["templates"] = templates
            await update_guild_cfg(gid, **fields)
            return await reply_text(interaction, f"🗑️ {module} deaktiviert.", kind="success")

        if module == "vc_override":
            if channel:
                await execute("DELETE FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_text(interaction, f"🗑️ vc_override entfernt für {channel.mention}.", kind="success")
            await execute("DELETE FROM vc_overrides WHERE guild_id=$1", gid)
            return await reply_text(interaction, "🗑️ Alle vc_override-Einträge entfernt.", kind="success")


async def setup(bot: commands.Bot):
    cog = AdminCog(bot)
    await bot.add_cog(cog)
    bot.tree.add_check(cog.ensure_lang_set)