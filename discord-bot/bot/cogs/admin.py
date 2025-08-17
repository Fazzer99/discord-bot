# bot/cogs/admin.py
from __future__ import annotations
import asyncio
import json
import discord
from discord import app_commands
from discord.ext import commands

from ..utils.checks import require_manage_guild
from ..utils.replies import reply_text, reply_error, reply_success
from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..db import execute, fetchrow
from ..utils.timezones import parse_utc_offset_to_minutes, format_utc_offset  # <— NEU

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
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        lang = (lang or "").strip().lower()
        if lang not in ("de", "en"):
            return await reply_error(interaction, "❌ Ungültige Sprache. Erlaubt: `de` oder `en`.", ephemeral=True)

        await update_guild_cfg(interaction.guild.id, lang=lang)
        msg = "✅ Sprache gesetzt auf **Deutsch**." if lang == "de" else "✅ Language set to **English**."
        return await reply_success(interaction, msg, ephemeral=True)

    # ---------------------------------------------------------------------
    # /onboard — einmalige Einrichtung: Sprache + UTC-Offset
    # ---------------------------------------------------------------------
    @app_commands.command(
        name="onboard",
        description="Einmalige Einrichtung: Sprache (de|en) und UTC-Offset (z. B. +2, -5.75, +4.5) setzen."
    )
    @require_manage_guild()
    @app_commands.describe(
        lang="de oder en",
        utc_offset="UTC-Offset in Stunden (Viertelstunden erlaubt): z. B. +2, -5.75, +4.5"
    )
    async def onboard(self, interaction: discord.Interaction, lang: str, utc_offset: float):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        lang = (lang or "").lower().strip()
        if lang not in ("de", "en"):
            return await reply_error(interaction, "❌ Ungültige Sprache. Erlaubt: `de` oder `en`.", ephemeral=True)

        tz_minutes = parse_utc_offset_to_minutes(utc_offset)
        if tz_minutes is None:
            return await reply_error(
                interaction,
                "❌ Ungültiger UTC-Offset. Erlaubt sind Viertelstunden in der Spanne **-12.0** bis **+14.0**.\n"
                "Beispiele: `+2`, `-5.75`, `+4.5`",
                ephemeral=True,
            )

        await update_guild_cfg(interaction.guild.id, lang=lang, tz=tz_minutes)
        return await reply_success(
            interaction,
            f"✅ Einrichtung abgeschlossen.\n"
            f"Sprache: **{'Deutsch' if lang == 'de' else 'English'}**, "
            f"Zeitzone: **{format_utc_offset(tz_minutes)}**.",
            ephemeral=True,
        )

    # ---------------------------------------------------------------------
    # /set_timezone — UTC-Offset separat ändern
    # ---------------------------------------------------------------------
    @app_commands.command(
        name="set_timezone",
        description="Setzt die Zeitzone als UTC-Offset (z. B. +2, -5.75, +4.5)."
    )
    @require_manage_guild()
    @app_commands.describe(utc_offset="UTC-Offset in Stunden (Viertelstunden erlaubt): z. B. +2, -5.75, +4.5")
    async def set_timezone(self, interaction: discord.Interaction, utc_offset: float):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        tz_minutes = parse_utc_offset_to_minutes(utc_offset)
        if tz_minutes is None:
            return await reply_error(
                interaction,
                "❌ Ungültiger UTC-Offset. Erlaubt sind Viertelstunden in der Spanne **-12.0** bis **+14.0**.",
                ephemeral=True,
            )

        await update_guild_cfg(interaction.guild.id, tz=tz_minutes)
        return await reply_success(
            interaction,
            f"🕒 Zeitzone auf **{format_utc_offset(tz_minutes)}** gesetzt.",
            ephemeral=True,
        )

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
            kind="warning",
            ephemeral=True,
        )
        raise app_commands.CheckFailure("Guild language not set")

    # ---------------------------------------------------------------------
    # /setup — Interaktives Setup (nur: welcome, leave)
    # ---------------------------------------------------------------------
    @app_commands.command(
        name="setup",
        description="Interaktives Setup: welcome, leave"
    )
    @require_manage_guild()
    @app_commands.describe(module="welcome | leave")
    async def setup(self, interaction: discord.Interaction, module: str):
        module = (module or "").lower()
        valid = ("welcome", "leave")
        if module not in valid:
            return await reply_error(interaction, "❌ Unbekanntes Modul.", ephemeral=True)

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "🧩 Setup gestartet. Ich stelle dir gleich ein paar Fragen in diesem Kanal. "
                "Falls die Zeit abläuft, kannst du einfach neu mit /setup starten.",
                ephemeral=True,
            )

        author = interaction.user
        channel = interaction.channel

        def _same_author_same_channel(m: discord.Message) -> bool:
            return (m.author == author) and (m.channel == channel)

        async def ask(
            prompt_de: str,
            *,
            want_channels: bool = False,
            want_roles: bool = False,
            accept_predicate=None,
            timeout: int = 60,
        ) -> discord.Message | None:
            await reply_text(channel, prompt_de, kind="info")
            def _check(m: discord.Message) -> bool:
                if not _same_author_same_channel(m):
                    return False
                if want_channels and not m.channel_mentions:
                    return False
                if want_roles and not m.role_mentions:
                    return False
                if accept_predicate and not accept_predicate(m):
                    return False
                return True
            try:
                return await self.bot.wait_for("message", check=_check, timeout=timeout)
            except asyncio.TimeoutError:
                await reply_text(
                    channel,
                    "⏰ Zeit abgelaufen. Setup abgebrochen. Starte `/setup` einfach erneut.",
                    kind="warning",
                )
                return None

        # welcome / leave
        msg_ch = await ask(f"❓ Bitte erwähne den Kanal für **{module}**-Nachrichten.", want_channels=True)
        if not msg_ch:
            return
        target_channel = msg_ch.channel_mentions[0]
        await update_guild_cfg(interaction.guild.id, **{f"{module}_channel": target_channel.id})

        if module == "welcome":
            msg_role = await ask("❓ Bitte erwähne die Rolle, die die Willkommens-Nachricht auslöst.", want_roles=True)
            if not msg_role:
                return
            await update_guild_cfg(interaction.guild.id, welcome_role=msg_role.role_mentions[0].id)

        if module in ("welcome", "leave"):
            msg2 = await ask(
                "✅ Kanal gesetzt. Bitte jetzt den Nachrichtentext eingeben.\n"
                "Platzhalter: `{member}` → Erwähnung, `{guild}` → Servername",
                timeout=300
            )
            if not msg2:
                return
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

        return await reply_success(channel, f"🎉 **{module}**-Setup abgeschlossen!")

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
            return await reply_error(interaction, "❌ Unbekanntes Modul.", ephemeral=True)

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        gid = interaction.guild.id

        if module == "autorole":
            await update_guild_cfg(gid, default_role=None)
            return await reply_success(interaction, "🗑️ Modul **autorole** wurde deaktiviert.", ephemeral=True)

        if module == "vc_track":
            if channel:
                await execute("DELETE FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_success(interaction, f"🗑️ VC-Tracking entfernt für {channel.mention}.", ephemeral=True)
            await execute("DELETE FROM vc_tracking WHERE guild_id=$1", gid)
            return await reply_success(interaction, "🗑️ VC-Tracking für **alle** Voice-Channels entfernt.", ephemeral=True)

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
            return await reply_success(interaction, f"🗑️ Modul **{module}** deaktiviert und Einstellungen gelöscht.", ephemeral=True)

        if module == "vc_override":
            if channel:
                await execute("DELETE FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_success(interaction, f"🗑️ vc_override-Overrides für {channel.mention} wurden entfernt.", ephemeral=True)
            await execute("DELETE FROM vc_overrides WHERE guild_id=$1", gid)
            return await reply_success(interaction, "🗑️ Alle vc_override-Overrides für diese Guild wurden entfernt.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))