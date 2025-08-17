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
from ..db import execute, fetchrow  # fetchrow bleibt importiert, falls du es später brauchst
from ..utils.timezones import validate_tz, guess_tz_from_locale, search_timezones  # NEU

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
            return await reply_text(interaction, "❌ Ungültige Sprache. Erlaubt: `de` oder `en`.", kind="error")

        await update_guild_cfg(interaction.guild.id, lang=lang)
        msg = "✅ Sprache gesetzt auf **Deutsch**." if lang == "de" else "✅ Language set to **English**."
        return await reply_text(interaction, msg, kind="success")

    # ---------------------------------------------------------------------
    # /onboard — einmalige Einrichtung: Sprache + Zeitzone
    # ---------------------------------------------------------------------
    @app_commands.autocomplete(
        tz=lambda inter, cur: [
            app_commands.Choice(name=z, value=z)
            for z in search_timezones(cur)[:25]
        ]
    )
    @app_commands.command(
        name="onboard",
        description="Einmalige Einrichtung: Sprache (de|en) und Zeitzone setzen."
    )
    @require_manage_guild()
    @app_commands.describe(
        lang="de oder en",
        tz="IANA-Zeitzone (z. B. Europe/Berlin, UTC …)"
    )
    async def onboard(self, interaction: discord.Interaction, lang: str, tz: str):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        lang = (lang or "").lower().strip()
        if lang not in ("de", "en"):
            return await reply_error(interaction, "❌ Ungültige Sprache. Erlaubt: `de` oder `en`.", ephemeral=True)

        tz_ok = validate_tz(tz)
        if not tz_ok:
            pref = getattr(interaction.guild, "preferred_locale", None) or getattr(interaction, "guild_locale", None)
            sug = guess_tz_from_locale(pref)
            return await reply_error(
                interaction,
                f"❌ Ungültige Zeitzone. Beispiele: `Europe/Berlin`, `UTC`.\nVorschlag: `{sug}`",
                ephemeral=True
            )

        await update_guild_cfg(interaction.guild.id, lang=lang, tz=tz_ok)
        return await reply_success(
            interaction,
            f"✅ Einrichtung abgeschlossen.\nSprache: **{'Deutsch' if lang=='de' else 'English'}**, Zeitzone: **{tz_ok}**.",
            ephemeral=True
        )

    # ---------------------------------------------------------------------
    # /set_timezone — Zeitzone separat ändern
    # ---------------------------------------------------------------------
    @app_commands.autocomplete(
        name=lambda inter, cur: [
            app_commands.Choice(name=z, value=z)
            for z in search_timezones(cur)[:25]
        ]
    )
    @app_commands.command(name="set_timezone", description="Zeitzone ändern (IANA-Name, z. B. Europe/Berlin).")
    @require_manage_guild()
    @app_commands.describe(name="IANA-Zeitzone")
    async def set_timezone(self, interaction: discord.Interaction, name: str):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        name_ok = validate_tz(name)
        if not name_ok:
            pref = getattr(interaction.guild, "preferred_locale", None) or getattr(interaction, "guild_locale", None)
            sug = guess_tz_from_locale(pref)
            return await reply_error(
                interaction,
                f"❌ Ungültige Zeitzone. Beispiele: `Europe/Berlin`, `UTC`.\nVorschlag: `{sug}`",
                ephemeral=True
            )

        await update_guild_cfg(interaction.guild.id, tz=name_ok)
        return await reply_success(interaction, f"🕒 Zeitzone auf **{name_ok}** gesetzt.", ephemeral=True)

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
    # /setup — Interaktives Setup (nur: welcome, leave)
    #  -> mit Timeout-freundlichem ask()-Helper
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
            return await reply_text(interaction, "❌ Unbekanntes Modul.", kind="error")

        # Interaction sofort quittieren, damit kein „… denkt nach …“ bleibt
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "🧩 Setup gestartet. Ich stelle dir gleich ein paar Fragen in diesem Kanal. "
                "Falls die Zeit abläuft, kannst du einfach neu mit /setup starten.",
                ephemeral=True,
            )

        author = interaction.user
        channel = interaction.channel

        # ---------- Helper ----------
        def _same_author_same_channel(m: discord.Message) -> bool:
            return (m.author == author) and (m.channel == channel)

        async def ask(
            prompt_de: str,
            *,
            want_channels: bool = False,
            want_roles: bool = False,
            accept_predicate = None,
            timeout: int = 60,
        ) -> discord.Message | None:
            """
            Schickt Prompt, wartet auf Antwort. Bei Timeout: freundliche Meldung, None zurück.
            - want_channels: Antwort muss Channel-Mentions enthalten
            - want_roles:    Antwort muss Role-Mentions enthalten
            - accept_predicate(msg): bool – zusätzliche Validierung (z.B. ja/nein)
            """
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

        # ---------- welcome / leave ----------
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

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        gid = interaction.guild.id

        if module == "autorole":
            await update_guild_cfg(gid, default_role=None)
            return await reply_text(interaction, "🗑️ Modul **autorole** wurde deaktiviert.", kind="success")

        if module == "vc_track":
            if channel:
                await execute("DELETE FROM vc_tracking WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_text(interaction, f"🗑️ VC-Tracking entfernt für {channel.mention}.", kind="success")
            await execute("DELETE FROM vc_tracking WHERE guild_id=$1", gid)
            return await reply_text(interaction, "🗑️ VC-Tracking für **alle** Voice-Channels entfernt.", kind="success")

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

        if module == "vc_override":
            if channel:
                await execute("DELETE FROM vc_overrides WHERE guild_id=$1 AND channel_id=$2", gid, channel.id)
                return await reply_text(interaction, f"🗑️ vc_override-Overrides für {channel.mention} wurden entfernt.", kind="success")
            await execute("DELETE FROM vc_overrides WHERE guild_id=$1", gid)
            return await reply_text(interaction, "🗑️ Alle vc_override-Overrides für diese Guild wurden entfernt.", kind="success")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))