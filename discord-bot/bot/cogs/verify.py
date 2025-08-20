# bot/cogs/verify.py
from __future__ import annotations
import time
import random
from typing import Optional, Dict, Any, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..utils.checks import require_manage_guild
from ..utils.replies import make_embed, send_embed, reply_success, reply_error, reply_text

VERIFY_SETTINGS_KEY = "verify"

# sichere Captcha-Zeichen (ohne 0/O/I/1)
CAPTCHA_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CAPTCHA_LEN_DEFAULT = 6
ATTEMPTS_DEFAULT = 3
COOLDOWN_DEFAULT = 5  # Sekunden
TTL_DEFAULT = 300     # 5 Min.

# --------------------------- Captcha-Modal ---------------------------

class CaptchaModal(discord.ui.Modal):
    def __init__(self, cog: "VerifyCog", key: Tuple[int, int], *, title: str = "Captcha Answer"):
        super().__init__(title=title, timeout=180)
        self.cog = cog
        self.key = key  # (guild_id, user_id)
        self.answer = discord.ui.TextInput(
            label="Answer",
            placeholder="Type the code here",
            required=True,
            max_length=16
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.validate_captcha_answer(interaction, self.key, self.answer.value)

# --------------------------- Ephemere Answer-View ---------------------------

class AnswerView(discord.ui.View):
    def __init__(self, cog: "VerifyCog", key: Tuple[int, int], *, timeout: Optional[float] = 300):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.key = key

    @discord.ui.button(label="Answer", style=discord.ButtonStyle.primary, custom_id="ignix_verify_answer")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CaptchaModal(self.cog, self.key))

# --------------------------- Haupt-View im Verify-Channel ---------------------------

class VerifyView(discord.ui.View):
    def __init__(self, cog: "VerifyCog", *, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.cog = cog

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="ignix_verify_button")
    async def verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_verify_click(interaction)

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, custom_id="ignix_verify_help")
    async def help_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("This must be used in a server.", ephemeral=True)
        cfg = await get_guild_cfg(guild.id)
        v = (cfg.get("settings") or {}).get(VERIFY_SETTINGS_KEY) or {}
        role_id = v.get("role_id")
        role_mention = f"<@&{role_id}>" if role_id else "`(not set)`"

        txt = (
            "🆘 **Verification Help**\n"
            f"• Press **Verify** to receive the access role {role_mention}.\n"
            "• If it fails, try again in a few seconds or contact a moderator.\n"
            "• Owner does not need to verify."
        )
        await interaction.response.send_message(txt, ephemeral=True)

# --------------------------- Cog ---------------------------

class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # cooldowns: user_id -> ts
        self.cooldowns: Dict[int, float] = {}
        # challenges: (guild_id, user_id) -> dict(state)
        self.challenges: Dict[Tuple[int, int], Dict[str, Any]] = {}

    # -------- /set_verify --------
    @app_commands.command(name="set_verify", description="Richtet die Verifizierung ein und postet die Verify-Nachricht.")
    @require_manage_guild()
    @app_commands.describe(
        channel="Kanal, in dem die Verify-Nachricht stehen soll",
        role="Rolle, die nach der Verifizierung vergeben wird",
        enabled="Feature aktivieren/deaktivieren",
        cooldown_seconds="Schutz vor Spam-Klicks (Standard 5s)",
        attempts="Erlaubte Fehlversuche (Standard 3)",
        ttl_seconds="Zeitfenster für einen Captcha (Standard 300s)",
        message_de="Optional: eigener deutscher Einleitungstext",
        message_en="Optional: eigener englischer Einleitungstext",
    )
    async def set_verify(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        enabled: bool = True,
        cooldown_seconds: int = COOLDOWN_DEFAULT,
        attempts: int = ATTEMPTS_DEFAULT,
        ttl_seconds: int = TTL_DEFAULT,
        message_de: Optional[str] = None,
        message_en: Optional[str] = None,
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if role >= interaction.guild.me.top_role:
            return await reply_error(
                interaction,
                "❌ Ich kann diese Rolle nicht vergeben (sie ist gleich/über meiner höchsten Rolle)."
            )

        settings = {
            "enabled": bool(enabled),
            "channel_id": channel.id,
            "role_id": role.id,
            "cooldown": max(0, int(cooldown_seconds)),
            "attempts": max(1, int(attempts)),
            "ttl": max(30, int(ttl_seconds)),
            "message_de": (message_de or "").strip() or (
                "🔒 **Verifizierung erforderlich!**\n"
                "Klicke **Verify**, dann **Answer** und gib den Code ein."
            ),
            "message_en": (message_en or "").strip() or (
                "🔒 **Verification required!**\n"
                "Click **Verify**, then **Answer** and enter the code."
            ),
        }
        # nur settings.verify aktualisieren
        await update_guild_cfg(interaction.guild.id, settings={VERIFY_SETTINGS_KEY: settings})

        emb = self._make_verify_embed(settings)
        view = VerifyView(self)
        try:
            await channel.send(embed=emb, view=view)
        except discord.Forbidden:
            return await reply_error(interaction, "❌ Ich darf in diesem Kanal nicht schreiben/Buttons posten.")
        except Exception:
            return await reply_error(interaction, "❌ Konnte die Verify-Nachricht nicht senden.")

        return await reply_success(
            interaction,
            f"✅ Verify eingerichtet in {channel.mention} (Rolle: {role.mention})."
        )

    # -------- /verify_config --------
    @app_commands.command(name="verify_config", description="Zeigt die aktuelle Verify-Konfiguration.")
    @require_manage_guild()
    async def verify_config(self, interaction: discord.Interaction):
        cfg = await get_guild_cfg(interaction.guild.id)
        v = (cfg.get("settings") or {}).get(VERIFY_SETTINGS_KEY) or {}
        ch = interaction.guild.get_channel(v.get("channel_id") or 0)
        rl = interaction.guild.get_role(v.get("role_id") or 0)

        desc = (
            f"**Aktiv:** {('ja' if v.get('enabled') else 'nein')}\n"
            f"**Kanal:** {ch.mention if isinstance(ch, discord.TextChannel) else '—'}\n"
            f"**Rolle:** {rl.mention if isinstance(rl, discord.Role) else '—'}\n"
            f"**Cooldown:** {v.get('cooldown', COOLDOWN_DEFAULT)}s\n"
            f"**Attempts:** {v.get('attempts', ATTEMPTS_DEFAULT)}\n"
            f"**TTL:** {v.get('ttl', TTL_DEFAULT)}s\n"
        )
        emb = make_embed(title="🔐 Verify – Konfiguration", description=desc, kind="info")
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # -------- Button: Verify --------
    async def handle_verify_click(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return await interaction.response.send_message("This must be used in a server.", ephemeral=True)

        cfg = await get_guild_cfg(guild.id)
        v = (cfg.get("settings") or {}).get(VERIFY_SETTINGS_KEY) or {}
        if not v.get("enabled"):
            return await interaction.response.send_message("Verification is currently disabled.", ephemeral=True)

        role_id = v.get("role_id")
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            return await interaction.response.send_message("Verify role is not configured.", ephemeral=True)

        # Owner braucht keine Verifizierung
        if guild.owner_id == user.id:
            return await interaction.response.send_message(
                "ℹ️ Du bist der Server-Owner – eine Verifizierung ist nicht nötig.", ephemeral=True
            )

        # Schon verifiziert?
        if role in user.roles:
            return await interaction.response.send_message("✅ Du bist bereits verifiziert.", ephemeral=True)

        # Cooldown
        now = time.time()
        cd = max(0, int(v.get("cooldown", COOLDOWN_DEFAULT)))
        until = self.cooldowns.get(user.id, 0.0)
        if now < until:
            return await interaction.response.send_message(
                f"⏳ Bitte kurz warten (~{int(until-now)}s) und erneut klicken.", ephemeral=True
            )
        self.cooldowns[user.id] = now + cd

        # Challenge generieren und ephemer posten
        code = self._gen_code(CAPTCHA_LEN_DEFAULT)
        key = (guild.id, user.id)
        self.challenges[key] = {
            "code": code,
            "expires": now + int(v.get("ttl", TTL_DEFAULT)),
            "attempts_left": int(v.get("attempts", ATTEMPTS_DEFAULT)),
            "role_id": role.id,
        }

        emb = self._make_challenge_embed(code)
        view = AnswerView(self, key)
        await interaction.response.send_message(embed=emb, view=view, ephemeral=True)

    # -------- Modal-Validierung --------
    async def validate_captcha_answer(self, interaction: discord.Interaction, key: Tuple[int, int], answer: str):
        guild = interaction.guild
        user = interaction.user
        if guild is None or not isinstance(user, discord.Member):
            return await interaction.response.send_message("This must be used in a server.", ephemeral=True)

        state = self.challenges.get(key)
        if not state:
            return await interaction.response.send_message("❌ Challenge abgelaufen. Bitte erneut **Verify** klicken.", ephemeral=True)

        # Ablauf prüfen
        if time.time() > state["expires"]:
            self.challenges.pop(key, None)
            return await interaction.response.send_message("⌛ Challenge abgelaufen. Bitte erneut **Verify** klicken.", ephemeral=True)

        # Antwort prüfen (case-insensitive)
        if (answer or "").strip().upper() != state["code"]:
            state["attempts_left"] -= 1
            if state["attempts_left"] <= 0:
                self.challenges.pop(key, None)
                return await interaction.response.send_message("❌ Zu viele Fehlversuche. Bitte kurz warten und neu beginnen.", ephemeral=True)

            # erneuter Versuch möglich
            emb = make_embed(
                title="❌ Falsche Antwort",
                description=f"Versuche übrig: **{state['attempts_left']}**",
                kind="warning"
            )
            view = AnswerView(self, key)
            return await interaction.response.send_message(embed=emb, view=view, ephemeral=True)

        # Korrekt → Rolle vergeben
        role = guild.get_role(state["role_id"])
        self.challenges.pop(key, None)
        if role is None:
            return await interaction.response.send_message("⚠️ Rolle nicht mehr vorhanden. Bitte melde dich bei einem Mod.", ephemeral=True)

        try:
            await user.add_roles(role, reason="Ignix Verify (captcha passed)")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Mir fehlen Rechte, um die Rolle zu vergeben (Rollen-Hierarchie prüfen).", ephemeral=True
            )
        except Exception:
            return await interaction.response.send_message("❌ Unerwarteter Fehler bei der Verifizierung.", ephemeral=True)

        await interaction.response.send_message("🎉 Verifizierung erfolgreich – willkommen!", ephemeral=True)

    # ----------------- Helper -----------------

    def _gen_code(self, length: int) -> str:
        return "".join(random.choice(CAPTCHA_CHARS) for _ in range(length))

    def _make_verify_embed(self, v: Dict[str, Any]) -> discord.Embed:
        de = v.get("message_de") or "🔒 **Verifizierung erforderlich!** Klicke **Verify**."
        en = v.get("message_en") or "🔒 **Verification required!** Click **Verify**."
        desc = f"{de}\n\n{en}"
        return make_embed(
            title="✅ Verification Required!",
            description=desc,
            kind="info",
        )

    def _make_challenge_embed(self, code: str) -> discord.Embed:
        # ephemere Challenge (an einzelne Person), mit „Answer“-Button in der View
        desc = (
            "**☘️ Are you human?**\n\n"
            f"Bitte **diesen Code** exakt eingeben (Groß/Klein egal):\n"
            f"```\n{code}\n```\n"
            "• Zeichne keine Linien – einfach nur den Code abtippen.\n"
            "• Du hast begrenzte Versuche; bei zu vielen Fehlversuchen kurzer Timeout."
        )
        return make_embed(description=desc, kind="info")

async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))