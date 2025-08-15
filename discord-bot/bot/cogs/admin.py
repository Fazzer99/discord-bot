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
from ..utils.checks import require_manage_guild

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------------------------------------------------
    # /setlang — setzt die Guild-Sprache (de | en)
    # ---------------------------------------------------------------------
    @app_commands.command(name="setlang", description="Setzt die Bot-Sprache für diesen Server (de|en)")
    @require_manage_guild()
    @app_commands.describe(lang="Zulässig: de | en")
    async def setlang(self, interaction: discord.Interaction, lang: str):
        lang = (lang or "").strip().lower()
        if lang not in ("de", "en"):
            return await reply_text(interaction, "❌ Ungültige Sprache. Erlaubt: `de` oder `en`.", kind="error")

        await update_guild_cfg(interaction.guild.id, lang=lang)
        msg = "✅ Sprache gesetzt auf **Deutsch**." if lang == "de" else "✅ Language set to **English**."
        return await reply_text(interaction, msg, kind="success")

    # ---------------------------------------------------------------------
    # Globaler Check als Funktion – wird per Dekorator an Commands gehängt
    # ---------------------------------------------------------------------
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
        # Abbruch
        raise app_commands.CheckFailure("Guild language not set")

    # ---------------------------------------------------------------------
    # /setup — Interaktiver Wizard (welcome, leave, vc_override, autorole, vc_track)
    # ---------------------------------------------------------------------
    @app_commands.command(
        name="setup",
        description="Interaktives Setup: welcome, leave, vc_override, autorole, vc_track"
    )
    @require_manage_guild()
    @app_commands.describe(module="welcome | leave | vc_override | autorole | vc_track")
    async def setup(self, interaction: discord.Interaction, module: str):
        module = (module or "").lower()
        valid = ("welcome", "leave", "vc_override", "autorole", "vc_track")
        if module not in valid:
            return await reply_text(interaction, "❌ Unbekanntes Modul.", kind="error")

        await interaction.response.defer()  # öffentlich
        author = interaction.user
        channel = interaction.channel

        def check_msg(msg: discord.Message, cond) -> bool:
            return msg.author == author and msg.channel == channel and cond(msg)

        # ---------- vc_override ----------
        if module == "vc_override":
            await reply_text(channel, "❓ Bitte erwähne den **Sprachkanal**.", kind="info")
            try:
                msg_chan = await self.bot.wait_for(
                    "message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60
                )
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `/setup vc_override` neu ausführen.", kind="warning")
            vc_channel = msg_chan.channel_mentions[0]

            # darf nicht parallel vc_track sein
            row = await fetchrow(
                "SELECT 1 FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if row:
                return await reply_text(
                    channel,
                    f"❌ Für {vc_channel.mention} ist bereits **vc_track** aktiv. "
                    f"Bitte zuerst `/disable vc_track channel:{vc_channel.name}` ausführen oder anderen Kanal wählen.",
                    kind="error"
                )

            await reply_text(channel, "❓ Bitte erwähne **Override-Rollen** (z. B. `@Admin @Moderator`).", kind="info")
            msg_o = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            override_ids = [r.id for r in msg_o.role_mentions]

            await reply_text(channel, "❓ Bitte erwähne **Ziel-Rollen**, die Zugriff erhalten sollen.", kind="info")
            msg_t = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            target_ids = [r.id for r in msg_t.role_mentions]

            await reply_text(channel, "❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).", kind="info")
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
            return await reply_text(
                channel,
                f"🎉 **vc_override**-Setup abgeschlossen für {vc_channel.mention}.",
                kind="success"
            )

        # ---------- vc_track ----------
        if module == "vc_track":
            await reply_text(channel, "❓ Bitte erwähne den **Sprachkanal**, den du tracken möchtest.", kind="info")
            msg_chan = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
            vc_channel = msg_chan.channel_mentions[0]

            # darf nicht parallel vc_override sein
            row = await fetchrow(
                "SELECT 1 FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if row:
                return await reply_text(
                    channel,
                    f"❌ Für {vc_channel.mention} ist bereits **vc_override** aktiv. "
                    f"Bitte zuerst `/disable vc_override channel:{vc_channel.name}` ausführen oder anderen Kanal wählen.",
                    kind="error"
                )

            cfg = await get_guild_cfg(interaction.guild.id)
            if not cfg.get("vc_log_channel"):
                await reply_text(channel, "❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).", kind="info")
                msg_log = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
                log_ch = msg_log.channel_mentions[0]
                await update_guild_cfg(interaction.guild.id, vc_log_channel=log_ch.id)

            exists = await fetchrow(
                "SELECT 1 FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if exists:
                return await reply_text(channel, f"ℹ️ **VC-Tracking** ist für {vc_channel.mention} bereits aktiv.", kind="info")

            # Deine Tabelle hat user_id NOT NULL -> Dummy 0 mitschreiben
            await execute(
                "INSERT INTO vc_tracking (guild_id, channel_id, user_id) VALUES ($1, $2, 0)",
                interaction.guild.id, vc_channel.id
            )
            return await reply_text(channel, f"🎉 **vc_track**-Setup abgeschlossen für {vc_channel.mention}.", kind="success")

        # ---------- autorole ----------
        if module == "autorole":
            await reply_text(channel, "❓ Bitte erwähne die Rolle, die neuen Mitgliedern automatisch zugewiesen werden soll.", kind="info")
            msg_r = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            autorole = msg_r.role_mentions[0]
            await update_guild_cfg(interaction.guild.id, default_role=autorole.id)
            return await reply_text(channel, f"🎉 **autorole**-Setup abgeschlossen! Neue Mitglieder bekommen {autorole.mention}.", kind="success")

        # ---------- welcome / leave ----------
        await reply_text(channel, f"❓ Bitte erwähne den Kanal für **{module}**-Nachrichten.", kind="info")
        msg = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.channel_mentions), timeout=60)
        target_channel = msg.channel_mentions[0]
        await update_guild_cfg(interaction.guild.id, **{f"{module}_channel": target_channel.id})

        if module == "welcome":
            await reply_text(channel, "❓ Bitte erwähne die Rolle, die die Willkommens-Nachricht auslöst.", kind="info")
            msgr = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: x.role_mentions), timeout=60)
            await update_guild_cfg(interaction.guild.id, welcome_role=msgr.role_mentions[0].id)

        if module in ("welcome", "leave"):
            await reply_text(
                channel,
                "✅ Kanal gesetzt. Bitte jetzt den Nachrichtentext eingeben.\n"
                "Platzhalter: `{member}` → Erwähnung, `{guild}` → Servername",
                kind="info"
            )
            msg2 = await self.bot.wait_for("message", check=lambda m: check_msg(m, lambda x: bool(x.content.strip())), timeout=300)

            cfg = await get_guild_cfg(interaction.guild.id)
            raw = cfg.get("templates") or {}
            if isinstance(raw, str):
                try:
                    current_templates = json.loads(raw)
                except json.JSONDecodeError:
                    current_templates = {}
            else:
                current_templates = dict(raw)
            current_templates[module] = msg2.content
            await update_guild_cfg(interaction.guild.id, templates=current_templates)

        return await reply_text(channel, f"🎉 **{module}**-Setup abgeschlossen!", kind="success")

    # ---------------------------------------------------------------------
    # /disable — Modul deaktivieren (optional: EIN Kanal für vc_*)
    # ---------------------------------------------------------------------
    @app_commands.command(name="disable", description="Deaktiviert ein Modul und entfernt zugehörige Daten")
    @require_manage_guild()
    @app_commands.describe(
        module="welcome | leave | vc_override | autorole | vc_track",
        channel="Optional: nur für einen bestimmten Kanal (bei vc_override/vc_track)"
    )
    async def disable(self, interaction: discord.Interaction, module: str, channel: discord.abc.GuildChannel | None = None):
        module = (module or "").lower()
        allowed = ("welcome", "leave", "vc_override", "autorole", "vc_track")
        if module not in allowed:
            return await reply_text(interaction, "❌ Unbekanntes Modul.", kind="error")

        gid = interaction.guild.id

        # autorole
        if module == "autorole":
            await update_guild_cfg(gid, default_role=None)
            return await reply_text(interaction, "🗑️ Modul **autorole** wurde deaktiviert.", kind="success")

        # vc_track
        if module == "vc_track":
            if channel:
                await execute("DELETE FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_text(interaction, f"🗑️ VC-Tracking entfernt für {channel.mention}.", kind="success")
            await execute("DELETE FROM vc_tracking WHERE guild_id=$1", gid)
            return await reply_text(interaction, "🗑️ VC-Tracking für **alle** Voice-Channels entfernt.", kind="success")

        # welcome / leave
        if module in ("welcome", "leave"):
            cfg = await get_guild_cfg(gid)
            fields = {}
            if module == "welcome":
                fields["welcome_channel"] = None
                fields["welcome_role"] = None
            else:
                fields["leave_channel"] = None

            tpl = cfg.get("templates") or {}
            if isinstance(tpl, str):
                try:
                    tpl = json.loads(tpl)
                except json.JSONDecodeError:
                    tpl = {}
            tpl.pop(module, None)
            fields["templates"] = tpl

            await update_guild_cfg(gid, **fields)
            return await reply_text(interaction, f"🗑️ Modul **{module}** deaktiviert und Einstellungen gelöscht.", kind="success")

        # vc_override
        if module == "vc_override":
            if channel:
                await execute("DELETE FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_text(interaction, f"🗑️ vc_override-Overrides für {channel.mention} wurden entfernt.", kind="success")
            await execute("DELETE FROM vc_overrides WHERE guild_id=$1", gid)
            return await reply_text(interaction, "🗑️ Alle vc_override-Overrides für diese Guild wurden entfernt.", kind="success")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))