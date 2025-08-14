# bot/cogs/admin.py
from __future__ import annotations
import asyncio
import json
import discord
from typing import List
from discord import app_commands
from discord.ext import commands
from ..utils.checks import require_manage_guild
from ..utils.replies import reply_text
from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..db import execute, fetchrow

class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    #
    # /setlang — 1:1 zu deinem alten !setlang, nur Slash + Embeds
    #
    @app_commands.command(name="setlang", description="Setzt die Bot-Sprache für diesen Server (de|en)")
    @require_manage_guild()
    @app_commands.describe(lang="Zulässig: de | en")
    async def setlang(self, interaction: discord.Interaction, lang: str):
        lang = (lang or "").strip().lower()
        if lang not in ("de", "en"):
            return await reply_text(
                interaction,
                "❌ Ungültige Sprache. Erlaubt: `de` oder `en`.",
                kind="error"
            )

        await update_guild_cfg(interaction.guild.id, lang=lang)

        if lang == "de":
            msg = "✅ Sprache gesetzt auf **Deutsch**. Deutsche Texte bleiben deutsch."
        else:
            msg = "✅ Language set to **English**. German texts will be auto-translated to English."

        return await reply_text(interaction, msg, kind="success")

    #
    # Globaler Slash-Check – entspricht deinem @bot.check ensure_lang_set
    #
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

        text = (
            "🌐 Bitte zuerst die Sprache wählen mit `/setlang de` oder `/setlang en`.\n"
            "🌐 Please choose a language first: `/setlang de` or `/setlang en`."
        )
        try:
            await reply_text(interaction, text, kind="warning")
        finally:
            raise app_commands.CheckFailure("Guild language not set")

    #
    # /setup — interaktiver Wizard (Welcome/Leave/vc_override/autorole/vc_track)
    #
    @app_commands.command(name="setup", description="Interaktives Setup für Module (welcome, leave, vc_override, autorole, vc_track)")
    @require_manage_guild()
    @app_commands.describe(module="Zulässig: welcome | leave | vc_override | autorole | vc_track")
    async def setup(self, interaction: discord.Interaction, module: str):
        module = (module or "").lower()
        valid = ("welcome", "leave", "vc_override", "autorole", "vc_track")
        if module not in valid:
            return await reply_text(
                interaction,
                "❌ Unbekanntes Modul. Verfügbar: `welcome`, `leave`, `vc_override`, `autorole`, `vc_track`.",
                kind="error"
            )

        await interaction.response.defer()  # öffentlich

        author = interaction.user
        channel = interaction.channel

        def _check_author_same_channel(msg: discord.Message) -> bool:
            return (msg.author == author) and (msg.channel == channel)

        # ─── vc_override ───────────────────────────────────────────────────────
        if module == "vc_override":
            await reply_text(channel, "❓ Bitte erwähne den **Sprachkanal**, für den das Override gelten soll.", kind="info")
            def check_chan(m: discord.Message):
                return _check_author_same_channel(m) and m.channel_mentions
            try:
                msg_chan = await self.bot.wait_for("message", check=check_chan, timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.", kind="warning")
            vc_channel = msg_chan.channel_mentions[0]

            row = await fetchrow(
                "SELECT 1 AS x FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if row:
                return await reply_text(
                    channel,
                    f"❌ Für {vc_channel.mention} ist bereits **vc_track** aktiv. Bitte zuerst `!disable vc_track` ausführen oder einen anderen Kanal wählen.",
                    kind="error"
                )

            await reply_text(channel, "❓ Bitte erwähne **Override-Rollen** (z.B. `@Admin @Moderator`).", kind="info")
            def check_override(m: discord.Message):
                return _check_author_same_channel(m) and m.role_mentions
            try:
                msg_o = await self.bot.wait_for("message", check=check_override, timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.", kind="warning")
            override_ids = [r.id for r in msg_o.role_mentions]

            await reply_text(channel, "❓ Bitte erwähne **Ziel-Rollen**, die automatisch Zugriff erhalten sollen.", kind="info")
            def check_target(m: discord.Message):
                return _check_author_same_channel(m) and m.role_mentions
            try:
                msg_t = await self.bot.wait_for("message", check=check_target, timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.", kind="warning")
            target_ids = [r.id for r in msg_t.role_mentions]

            await reply_text(channel, "❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).", kind="info")
            def check_vclog(m: discord.Message):
                return _check_author_same_channel(m) and m.channel_mentions
            try:
                msg_log = await self.bot.wait_for("message", check=check_vclog, timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup vc_override` neu ausführen.", kind="warning")
            vc_log_channel = msg_log.channel_mentions[0]

            await update_guild_cfg(interaction.guild.id, vc_log_channel=vc_log_channel.id)

            await execute(
                """
                INSERT INTO vc_overrides (guild_id, channel_id, override_roles, target_roles)
                VALUES ($1, $2, $3::jsonb, $4::jsonb)
                ON CONFLICT (guild_id, channel_id) DO UPDATE
                  SET override_roles = EXCLUDED.override_roles,
                      target_roles   = EXCLUDED.target_roles;
                """,
                interaction.guild.id,
                vc_channel.id,
                json.dumps(override_ids),
                json.dumps(target_ids),
            )

            return await reply_text(
                channel,
                f"🎉 **vc_override**-Setup abgeschlossen für {vc_channel.mention}!\nOverride-Rollen und Ziel-Rollen wurden gespeichert.",
                kind="success"
            )

        # ─── vc_track ─────────────────────────────────────────────────────────
        if module == "vc_track":
            await reply_text(channel, "❓ Bitte erwähne den **Sprachkanal**, den du tracken möchtest.", kind="info")
            def check_chan2(m: discord.Message):
                return _check_author_same_channel(m) and m.channel_mentions
            try:
                msg_chan = await self.bot.wait_for("message", check=check_chan2, timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup vc_track` neu ausführen.", kind="warning")
            vc_channel = msg_chan.channel_mentions[0]

            row = await fetchrow(
                "SELECT 1 AS x FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if row:
                return await reply_text(
                    channel,
                    f"❌ Für {vc_channel.mention} ist bereits **vc_override** aktiv. Bitte zuerst `!disable vc_override` (optional mit Kanal) ausführen oder einen anderen Kanal wählen.",
                    kind="error"
                )

            cfg = await get_guild_cfg(interaction.guild.id)
            if not cfg.get("vc_log_channel"):
                await reply_text(channel, "❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).", kind="info")
                def check_vclog2(m: discord.Message):
                    return _check_author_same_channel(m) and m.channel_mentions
                try:
                    msg_log = await self.bot.wait_for("message", check=check_vclog2, timeout=60)
                except asyncio.TimeoutError:
                    return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup vc_track` neu ausführen.", kind="warning")
                log_ch = msg_log.channel_mentions[0]
                await update_guild_cfg(interaction.guild.id, vc_log_channel=log_ch.id)

            row2 = await fetchrow(
                "SELECT 1 AS x FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if row2:
                return await reply_text(channel, f"ℹ️ **VC-Tracking** ist für {vc_channel.mention} bereits aktiv.", kind="info")

            await execute(
                "INSERT INTO vc_tracking (guild_id, channel_id) VALUES ($1, $2)",
                interaction.guild.id, vc_channel.id
            )

            return await reply_text(channel, f"🎉 **vc_track**-Setup abgeschlossen für {vc_channel.mention}.", kind="success")

        # ─── autorole ─────────────────────────────────────────────────────────
        if module == "autorole":
            await reply_text(channel, "❓ Bitte erwähne die Rolle, die neuen Mitgliedern automatisch zugewiesen werden soll.", kind="info")
            def check_role(m: discord.Message):
                return _check_author_same_channel(m) and m.role_mentions
            try:
                msg_r = await self.bot.wait_for("message", check=check_role, timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup autorole` neu ausführen.", kind="warning")
            autorole = msg_r.role_mentions[0]
            await update_guild_cfg(interaction.guild.id, default_role=autorole.id)
            return await reply_text(
                channel,
                f"🎉 **autorole**-Setup abgeschlossen! Neue Mitglieder bekommen die Rolle {autorole.mention}.",
                kind="success"
            )

        # ─── Gemeinsames Setup: Kanal abfragen (welcome/leave) ─────────────────
        await reply_text(channel, f"❓ Bitte erwähne den Kanal für **{module}**-Nachrichten.", kind="info")
        def check_chan3(m: discord.Message):
            return _check_author_same_channel(m) and m.channel_mentions
        try:
            msg = await self.bot.wait_for("message", check=check_chan3, timeout=60)
        except asyncio.TimeoutError:
            return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup` neu ausführen.", kind="warning")
        target_channel = msg.channel_mentions[0]
        await update_guild_cfg(interaction.guild.id, **{f"{module}_channel": target_channel.id})

        if module == "welcome":
            await reply_text(channel, "❓ Bitte erwähne die Rolle, die die Willkommens-Nachricht auslöst.", kind="info")
            def check_role2(m: discord.Message):
                return _check_author_same_channel(m) and m.role_mentions
            try:
                msgr = await self.bot.wait_for("message", check=check_role2, timeout=60)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup welcome` neu ausführen.", kind="warning")
            await update_guild_cfg(interaction.guild.id, welcome_role=msgr.role_mentions[0].id)

        if module in ("welcome", "leave"):
            await reply_text(
                channel,
                f"✅ Kanal gesetzt auf {target_channel.mention}. Jetzt den Nachrichtentext eingeben.\nVerwende Platzhalter:\n`{{member}}` → Member-Erwähnung\n`{{guild}}`  → Server-Name",
                kind="info"
            )
            def check_txt(m: discord.Message):
                return _check_author_same_channel(m) and (m.content.strip() != "")
            try:
                msg2 = await self.bot.wait_for("message", check=check_txt, timeout=300)
            except asyncio.TimeoutError:
                return await reply_text(channel, "⏰ Zeit abgelaufen. Bitte `!setup` neu ausführen.", kind="warning")

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

    #
    # /disable — 1:1 Port von !disable, mit Embeds & Farben
    #
    @app_commands.command(name="disable", description="Deaktiviert ein Modul und entfernt zugehörige Daten")
    @require_manage_guild()
    @app_commands.describe(
        module="welcome | leave | vc_override | autorole | vc_track",
        channels="(Optional) Kanäle für vc_override/vc_track (mehrere möglich)"
    )
    async def disable(
        self,
        interaction: discord.Interaction,
        module: str,
        channels: List[discord.abc.GuildChannel] = None  # Hinweis: Slash-UI unterstützt evtl. nur einen Channel
    ):
        """
        Deaktiviert ein Modul und entfernt alle zugehörigen Daten.
        Usage:
          • /disable module:welcome
          • /disable module:leave
          • /disable module:vc_override channels:[#Voice1 …]
        """
        module = (module or "").lower()
        allowed = ("welcome", "leave", "vc_override", "autorole", "vc_track")
        if module not in allowed:
            return await reply_text(
                interaction,
                "❌ Unbekanntes Modul. Erlaubt: `welcome`, `leave`, `vc_override`, `autorole`, `vc_track`.",
                kind="error"
            )

        guild_id = interaction.guild.id

        # autorole deaktivieren
        if module == "autorole":
            await update_guild_cfg(guild_id, default_role=None)
            return await reply_text(
                interaction,
                "🗑️ Modul **autorole** wurde deaktiviert. Keine Autorole mehr gesetzt.",
                kind="success"
            )

        # vc_track deaktivieren
        if module == "vc_track":
            if channels:
                removed = []
                for ch in channels:
                    if isinstance(ch, discord.VoiceChannel):
                        await execute(
                            "DELETE FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                            guild_id, ch.id
                        )
                        removed.append(ch.mention)
                if removed:
                    return await reply_text(
                        interaction,
                        f"🗑️ VC-Tracking entfernt für: {', '.join(removed)}",
                        kind="success"
                    )
                return await reply_text(interaction, "ℹ️ Keine gültigen Voice-Channels angegeben.", kind="info")
            else:
                await execute("DELETE FROM vc_tracking WHERE guild_id=$1", guild_id)
                return await reply_text(
                    interaction,
                    "🗑️ VC-Tracking für **alle** Voice-Channels entfernt.",
                    kind="success"
                )

        # welcome & leave: Channel/Role/Template entfernen
        if module in ("welcome", "leave"):
            cfg = await get_guild_cfg(guild_id)
            fields = {}
            if module == "welcome":
                fields["welcome_channel"] = None
                fields["welcome_role"]    = None
            else:
                fields["leave_channel"]   = None

            tpl = (cfg.get("templates") or {}).copy()
            if isinstance(tpl, str):
                try:
                    tpl = json.loads(tpl)
                except json.JSONDecodeError:
                    tpl = {}
            tpl.pop(module, None)
            fields["templates"] = tpl

            await update_guild_cfg(guild_id, **fields)
            return await reply_text(
                interaction,
                f"🗑️ Modul **{module}** wurde deaktiviert und alle Einstellungen gelöscht.",
                kind="success"
            )

        # vc_override
        if channels:
            removed = []
            for ch in channels:
                await execute(
                    "DELETE FROM vc_overrides WHERE guild_id = $1 AND channel_id = $2",
                    guild_id, ch.id
                )
                removed.append(ch.mention)
            return await reply_text(
                interaction,
                f"🗑️ vc_override-Overrides für {', '.join(removed)} wurden entfernt.",
                kind="success"
            )

        await execute("DELETE FROM vc_overrides WHERE guild_id = $1", guild_id)
        return await reply_text(
            interaction,
            "🗑️ Alle vc_override-Overrides für diese Guild wurden entfernt.",
            kind="success"
        )

async def setup(bot: commands.Bot):
    cog = AdminCog(bot)
    await bot.add_cog(cog)
    bot.tree.add_check(cog.ensure_lang_set)