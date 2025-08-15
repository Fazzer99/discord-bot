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
    # /setup — Interaktives Setup (welcome, leave, vc_override, autorole, vc_track)
    #  -> mit Timeout-freundlichem ask()-Helper
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

        # Interaction sofort quittieren, damit kein „… denkt nach …“ bleibt
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "🧩 Setup gestartet. Ich stelle dir gleich ein paar Fragen in diesem Kanal. "
                "Mit `abbrechen` oder nach Timeout kannst du einfach neu mit `/setup` starten.",
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

        # auch praktisch:
        async def ask_voice_channel(prompt: str) -> discord.VoiceChannel | None:
            msg = await ask(prompt, want_channels=True)
            if not msg:
                return None
            vc = msg.channel_mentions[0]
            if not isinstance(vc, discord.VoiceChannel):
                await reply_text(channel, "❌ Das war kein Sprachkanal.", kind="error")
                return None
            return vc

        async def ask_text_channel(prompt: str) -> discord.TextChannel | None:
            msg = await ask(prompt, want_channels=True)
            if not msg:
                return None
            tc = msg.channel_mentions[0]
            if not isinstance(tc, discord.TextChannel):
                await reply_text(channel, "❌ Das war kein Textkanal.", kind="error")
                return None
            return tc

        # ---------- vc_override ----------
        if module == "vc_override":
            vc_channel = await ask_voice_channel("❓ Bitte erwähne den **Sprachkanal**, für den das Override gelten soll.")
            if not vc_channel:
                return

            # darf nicht parallel vc_track sein
            row = await fetchrow(
                "SELECT 1 FROM public.vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if row:
                return await reply_text(
                    channel,
                    f"❌ Für {vc_channel.mention} ist bereits **vc_track** aktiv. "
                    f"Bitte zuerst `/disable module:vc_track channel:{vc_channel.name}` ausführen oder einen anderen Kanal wählen.",
                    kind="error"
                )

            msg_o = await ask("❓ Bitte erwähne **Override-Rollen** (z. B. `@Admin @Moderator`).", want_roles=True)
            if not msg_o:
                return
            override_ids = [r.id for r in msg_o.role_mentions]

            msg_t = await ask("❓ Bitte erwähne **Ziel-Rollen**, die automatisch Zugriff erhalten sollen.", want_roles=True)
            if not msg_t:
                return
            target_ids = [r.id for r in msg_t.role_mentions]

            # Log-Kanal behalten/ändern
            cfg = await get_guild_cfg(interaction.guild.id)
            current_log_id = cfg.get("vc_log_channel")
            current_log_ch = interaction.guild.get_channel(current_log_id) if current_log_id else None

            if isinstance(current_log_ch, discord.TextChannel):
                keep = await ask(
                    f"ℹ️ Aktueller Log-Kanal ist {current_log_ch.mention}. "
                    f"Möchtest du **diesen** weiter verwenden? Antworte mit `ja` oder `nein`.",
                    accept_predicate=lambda m: m.content.lower().strip() in {"ja","j","yes","y","nein","n","no"},
                    timeout=45
                )
                if not keep:
                    return
                use_keep = keep.content.lower().strip() in {"ja","j","yes","y"}
                if not use_keep:
                    log_ch = await ask_text_channel("❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).")
                    if not log_ch:
                        return
                    await update_guild_cfg(interaction.guild.id, vc_log_channel=log_ch.id)
                else:
                    log_ch = current_log_ch
            else:
                log_ch = await ask_text_channel("❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).")
                if not log_ch:
                    return
                await update_guild_cfg(interaction.guild.id, vc_log_channel=log_ch.id)

            await execute(
                """
                INSERT INTO public.vc_overrides (guild_id, channel_id, override_roles, target_roles)
                VALUES ($1, $2, $3::jsonb, $4::jsonb)
                ON CONFLICT (guild_id, channel_id) DO UPDATE
                  SET override_roles = EXCLUDED.override_roles,
                      target_roles   = EXCLUDED.target_roles
                """,
                interaction.guild.id, vc_channel.id, json.dumps(override_ids), json.dumps(target_ids)
            )

            return await reply_text(
                channel,
                f"🎉 **vc_override** aktiviert für {vc_channel.mention}.\n"
                f"🔐 Override-Rollen gesetzt, Ziel-Rollen hinterlegt.\n"
                f"🧾 Live-Logs gehen nach {log_ch.mention}.",
                kind="success"
            )

        # ---------- vc_track ----------
        if module == "vc_track":
            vc_channel = await ask_voice_channel("❓ Bitte erwähne den **Sprachkanal**, den du tracken möchtest.")
            if not vc_channel:
                return

            exists_override = await fetchrow(
                "SELECT 1 FROM public.vc_overrides WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if exists_override:
                return await reply_text(
                    channel,
                    f"❌ Für {vc_channel.mention} ist bereits **vc_override** aktiv. "
                    f"Bitte zuerst `/disable module:vc_override channel:{vc_channel.name}` ausführen oder einen anderen Kanal wählen.",
                    kind="error"
                )

            cfg = await get_guild_cfg(interaction.guild.id)
            current_log_id = cfg.get("vc_log_channel")
            current_log_ch = interaction.guild.get_channel(current_log_id) if current_log_id else None

            if isinstance(current_log_ch, discord.TextChannel):
                keep = await ask(
                    f"ℹ️ Aktueller Log-Kanal ist {current_log_ch.mention}. "
                    f"Möchtest du **diesen** weiter verwenden? Antworte mit `ja` oder `nein`.",
                    accept_predicate=lambda m: m.content.lower().strip() in {"ja","j","yes","y","nein","n","no"},
                    timeout=45
                )
                if not keep:
                    return
                use_keep = keep.content.lower().strip() in {"ja","j","yes","y"}
                if not use_keep:
                    new_log = await ask_text_channel("❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).")
                    if not new_log:
                        return
                    await update_guild_cfg(interaction.guild.id, vc_log_channel=new_log.id)
                    log_ch = new_log
                else:
                    log_ch = current_log_ch
            else:
                new_log = await ask_text_channel("❓ Bitte erwähne den **Kanal für Live-VC-Logs** (z. B. `#modlogs`).")
                if not new_log:
                    return
                await update_guild_cfg(interaction.guild.id, vc_log_channel=new_log.id)
                log_ch = new_log

            exists = await fetchrow(
                "SELECT 1 FROM public.vc_tracking WHERE guild_id=$1 AND channel_id=$2",
                interaction.guild.id, vc_channel.id
            )
            if exists:
                return await reply_text(
                    channel,
                    f"ℹ️ **VC-Tracking** ist für {vc_channel.mention} bereits aktiv. (Log-Kanal: {log_ch.mention})",
                    kind="info"
                )

            await execute(
                "INSERT INTO public.vc_tracking (guild_id, channel_id) VALUES ($1, $2)",
                interaction.guild.id, vc_channel.id
            )

            return await reply_text(
                channel,
                f"🎉 **vc_track** aktiviert für {vc_channel.mention}.\n"
                f"🧾 Live-Logs gehen nach {log_ch.mention}.",
                kind="success"
            )

        # ---------- autorole ----------
        if module == "autorole":
            msg_r = await ask("❓ Bitte erwähne die Rolle, die neuen Mitgliedern automatisch zugewiesen werden soll.",
                              want_roles=True)
            if not msg_r:
                return
            autorole = msg_r.role_mentions[0]
            await update_guild_cfg(interaction.guild.id, default_role=autorole.id)
            return await reply_text(
                channel,
                f"🎉 **autorole**-Setup abgeschlossen! Neue Mitglieder bekommen {autorole.mention}.",
                kind="success"
            )

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