# bot/cogs/verify.py
from __future__ import annotations
import io
import time
import random
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from ..services.guild_config import get_guild_cfg, update_guild_cfg
from ..utils.checks import require_manage_guild
from ..utils.replies import make_embed, reply_success, reply_error
from ..db import fetchrow, execute

# Pillow (für Bild-Captcha) optional
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    _PIL_OK = True
except Exception:
    _PIL_OK = False

VERIFY_SETTINGS_KEY = "verify"

# Zeichen ohne Verwechslungsgefahr (keine 0/O, I/l/1, S/5, B/8 …)
SAFE_UPPER = "2345679ACDEFGHJKLMNPQRTUVWXYZ"  # ohne O,I,S,B,8,5,0,1
SAFE_LOWER = "acdefghjkmnpqrtuvwxyz"          # gematcht zu oben (keine l,i,o,s,b)
SAFE_DIGIT = "234679"

DEFAULT_CODE_LEN = 6
DEFAULT_ATTEMPTS = 3
DEFAULT_COOLDOWN = 5     # Sekunden
DEFAULT_TTL = 300        # 5 Min.
DEFAULT_MODE = "image"   # "image" | "text"
DEFAULT_CASE_SENSITIVE = True

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
            max_length=32
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
        if not interaction.guild:
            return await interaction.response.send_message("This must be used in a server.", ephemeral=True)
        txt = (
            "🆘 **Verification Help**\n"
            "• Press **Verify** to start, then **Answer** to type the code.\n"
            "• If it fails, try again in a few seconds or contact a moderator.\n"
            "• Server owners don’t need to verify."
        )
        await interaction.response.send_message(txt, ephemeral=True)

# --------------------------- Cog ---------------------------

class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cooldowns: Dict[int, float] = {}
        self.challenges: Dict[Tuple[int, int], Dict[str, Any]] = {}

    # -------- /set_verify --------
    @app_commands.command(
        name="set_verify",
        description="Richtet die Verifizierung ein und postet die Verify-Nachricht."
    )
    @require_manage_guild()
    @app_commands.describe(
        channel="Kanal, in dem die Verify-Nachricht stehen soll",
        enabled="Feature aktivieren/deaktivieren",
        cooldown_seconds="Schutz vor Spam-Klicks (Standard 5s)",
        attempts="Erlaubte Fehlversuche (Standard 3)",
        ttl_seconds="Zeitfenster für einen Captcha (Standard 300s)",
        code_length=f"Länge des Codes (Standard {DEFAULT_CODE_LEN})",
        mode='Captcha-Modus: "image" (Standard) oder "text"',
        case_sensitive="Groß-/Kleinschreibung beachten? (Standard: Ja)",
        message_de="Optional: eigener deutscher Einleitungstext",
        message_en="Optional: eigener englischer Einleitungstext",
    )
    async def set_verify(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        enabled: bool = True,
        cooldown_seconds: int = DEFAULT_COOLDOWN,
        attempts: int = DEFAULT_ATTEMPTS,
        ttl_seconds: int = DEFAULT_TTL,
        code_length: int = DEFAULT_CODE_LEN,
        mode: str = DEFAULT_MODE,
        case_sensitive: bool = DEFAULT_CASE_SENSITIVE,
        message_de: Optional[str] = None,
        message_en: Optional[str] = None,
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        mode = (mode or "").lower().strip()
        if mode not in ("image", "text"):
            mode = DEFAULT_MODE

        # existierende Settings mergen
        cfg = await get_guild_cfg(interaction.guild.id)
        all_settings = (cfg.get("settings") or {}).copy()

        verify_settings = {
            "enabled": bool(enabled),
            "channel_id": channel.id,
            "cooldown": max(0, int(cooldown_seconds)),
            "attempts": max(1, int(attempts)),
            "ttl": max(30, int(ttl_seconds)),
            "code_length": max(4, min(12, int(code_length))),
            "mode": mode,
            "case_sensitive": bool(case_sensitive),
            "message_de": (message_de or "").strip() or (
                "🔒 **Verifizierung erforderlich!**\n"
                "Klicke **Verify**, dann **Answer** und gib den Code aus dem Bild ein."
            ),
            "message_en": (message_en or "").strip() or (
                "🔒 **Verification required!**\n"
                "Click **Verify**, then **Answer** and enter the code from the image."
            ),
        }
        all_settings[VERIFY_SETTINGS_KEY] = verify_settings
        await update_guild_cfg(interaction.guild.id, settings=all_settings)

        emb = self._make_verify_embed(verify_settings)
        view = VerifyView(self)
        try:
            await channel.send(embed=emb, view=view)
        except discord.Forbidden:
            return await reply_error(interaction, "❌ Ich darf in diesem Kanal nicht schreiben/Buttons posten.")
        except Exception:
            return await reply_error(interaction, "❌ Konnte die Verify-Nachricht nicht senden.")

        hint = ""
        if verify_settings["mode"] == "image" and not _PIL_OK:
            hint = " (Hinweis: Pillow nicht installiert – es wird automatisch Text-Captcha verwendet.)"

        return await reply_success(
            interaction,
            f"✅ Verify eingerichtet in {channel.mention}.{hint}"
        )

    # -------- /verify_config --------
    @app_commands.command(name="verify_config", description="Zeigt die aktuelle Verify-Konfiguration.")
    @require_manage_guild()
    async def verify_config(self, interaction: discord.Interaction):
        cfg = await get_guild_cfg(interaction.guild.id)
        v = (cfg.get("settings") or {}).get(VERIFY_SETTINGS_KEY) or {}
        ch = interaction.guild.get_channel(v.get("channel_id") or 0)

        desc = (
            f"**Aktiv:** {('ja' if v.get('enabled') else 'nein')}\n"
            f"**Kanal:** {ch.mention if isinstance(ch, discord.TextChannel) else '—'}\n"
            f"**Cooldown:** {v.get('cooldown', DEFAULT_COOLDOWN)}s\n"
            f"**Attempts:** {v.get('attempts', DEFAULT_ATTEMPTS)}\n"
            f"**TTL:** {v.get('ttl', DEFAULT_TTL)}s\n"
            f"**Mode:** {v.get('mode', DEFAULT_MODE)}\n"
            f"**Case sensitive:** {('ja' if v.get('case_sensitive', DEFAULT_CASE_SENSITIVE) else 'nein')}\n"
            f"**Code length:** {v.get('code_length', DEFAULT_CODE_LEN)}\n"
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

        if guild.owner_id == user.id:
            return await interaction.response.send_message(
                "ℹ️ Du bist der Server-Owner – eine Verifizierung ist nicht nötig.", ephemeral=True
            )

        # Schon verifiziert?
        row = await fetchrow(
            "SELECT 1 FROM public.verify_passed WHERE guild_id=$1 AND user_id=$2",
            guild.id, user.id
        )
        if row:
            return await interaction.response.send_message("✅ Du bist bereits verifiziert.", ephemeral=True)

        # Cooldown
        now = time.time()
        cd = max(0, int(v.get("cooldown", DEFAULT_COOLDOWN)))
        until = self.cooldowns.get(user.id, 0.0)
        if now < until:
            return await interaction.response.send_message(
                f"⏳ Bitte kurz warten (~{int(until-now)}s) und erneut klicken.", ephemeral=True
            )
        self.cooldowns[user.id] = now + cd

        # Challenge generieren
        code_len = int(v.get("code_length", DEFAULT_CODE_LEN))
        case_sensitive = bool(v.get("case_sensitive", DEFAULT_CASE_SENSITIVE))
        code = self._gen_code(code_len, case_sensitive)

        key = (guild.id, user.id)
        self.challenges[key] = {
            "code": code,                    # exakt wie generiert (ggf. gemischt)
            "case_sensitive": case_sensitive,
            "expires": now + int(v.get("ttl", DEFAULT_TTL)),
            "attempts_left": int(v.get("attempts", DEFAULT_ATTEMPTS)),
            "mode": v.get("mode", DEFAULT_MODE),
        }

        mode = self.challenges[key]["mode"]
        if mode == "image" and _PIL_OK:
            fobj = self._make_image_captcha(code)
            emb = make_embed(
                title="☘️ Are you human?",
                description=("Tip: Beachte Groß/Kleinschreibung." if case_sensitive
                             else "Hinweis: Groß/Kleinschreibung ist egal."),
                kind="info",
            )
            view = AnswerView(self, key)
            await interaction.response.send_message(embed=emb, file=fobj, view=view, ephemeral=True)
        else:
            # Text-Fallback
            emb = self._make_text_challenge_embed(code, case_sensitive)
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

        if time.time() > state["expires"]:
            self.challenges.pop(key, None)
            return await interaction.response.send_message("⌛ Challenge abgelaufen. Bitte erneut **Verify** klicken.", ephemeral=True)

        expected = state["code"]
        case_sensitive = state.get("case_sensitive", True)
        given = (answer or "").strip()

        ok = (given == expected) if case_sensitive else (given.upper() == expected.upper())

        if not ok:
            state["attempts_left"] -= 1
            if state["attempts_left"] <= 0:
                self.challenges.pop(key, None)
                return await interaction.response.send_message("❌ Zu viele Fehlversuche. Bitte kurz warten und neu beginnen.", ephemeral=True)

            emb = make_embed(
                title="❌ Falsche Antwort",
                description=f"Versuche übrig: **{state['attempts_left']}**",
                kind="warning"
            )
            view = AnswerView(self, key)
            return await interaction.response.send_message(embed=emb, view=view, ephemeral=True)

        # ✅ korrekt -> Eintrag in UTC speichern (naiv)
        self.challenges.pop(key, None)
        utc_now = datetime.utcnow()
        await execute(
            """
            INSERT INTO public.verify_passed (guild_id, user_id, passed_at)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id) DO NOTHING
            """,
            guild.id, user.id, utc_now
        )

        # lokale Anzeige
        cfg = await get_guild_cfg(guild.id)
        try:
            tz_minutes = int(str(cfg.get("tz") or "0").strip())
        except Exception:
            tz_minutes = 0
        tz_minutes = max(-840, min(840, tz_minutes))
        local_now = utc_now + timedelta(minutes=tz_minutes)

        await interaction.response.send_message(
            f"🎉 Verifizierung erfolgreich – willkommen!\n"
            f"🕒 Zeit (Server-lokal): **{local_now:%d.%m.%Y %H:%M:%S}**",
            ephemeral=True,
        )

    # -------- /verify_info --------
    @app_commands.command(
        name="verify_info",
        description="Zeigt, wann ein Mitglied verifiziert wurde (lokale Serverzeit)."
    )
    @require_manage_guild()
    @app_commands.describe(member="Mitglied, dessen Verifizierungszeit angezeigt werden soll")
    async def verify_info(self, interaction: discord.Interaction, member: discord.Member):
        row = await fetchrow(
            "SELECT passed_at FROM public.verify_passed WHERE guild_id=$1 AND user_id=$2",
            interaction.guild.id, member.id
        )
        if not row or not row.get("passed_at"):
            return await interaction.response.send_message(
                f"ℹ️ {member.mention} ist **nicht** verifiziert (kein Eintrag gefunden).",
                ephemeral=True
            )

        passed_utc = row["passed_at"]  # naive UTC
        cfg = await get_guild_cfg(interaction.guild.id)
        try:
            tz_minutes = int(str(cfg.get("tz") or "0").strip())
        except Exception:
            tz_minutes = 0
        tz_minutes = max(-840, min(840, tz_minutes))
        local_time = passed_utc + timedelta(minutes=tz_minutes)

        emb = make_embed(
            title="🔎 Verify-Info",
            description=(
                f"👤 Mitglied: {member.mention}\n"
                f"🕒 Verifiziert am: **{local_time:%d.%m.%Y %H:%M:%S}** (Server-Zeit)\n"
                f"↪ gespeichert als UTC: {passed_utc:%Y-%m-%d %H:%M:%S}"
            ),
            kind="info",
        )
        await interaction.response.send_message(embed=emb, ephemeral=True)

    # ----------------- Helper -----------------

    def _gen_code(self, length: int, case_sensitive: bool) -> str:
        """Gemischter Code: Ziffern + (ggf.) Groß/kleinbuchstaben aus 'sicheren' Sets."""
        alphabet = SAFE_DIGIT + SAFE_UPPER + (SAFE_LOWER if case_sensitive else "")
        return "".join(random.choice(alphabet) for _ in range(length))

    def _make_verify_embed(self, v: Dict[str, Any]) -> discord.Embed:
        de = v.get("message_de") or "🔒 **Verifizierung erforderlich!** Klicke **Verify**."
        en = v.get("message_en") or "🔒 **Verification required!** Click **Verify**."
        desc = f"{de}\n\n{en}"
        return make_embed(
            title="✅ Verification Required!",
            description=desc,
            kind="info",
        )

    def _make_text_challenge_embed(self, code: str, case_sensitive: bool) -> discord.Embed:
        hint = "Beachte Groß/Kleinschreibung." if case_sensitive else "Groß/Kleinschreibung ist egal."
        desc = (
            f"**☘️ Are you human?**\n\n"
            f"{hint}\n\n"
            f"**Code:**\n```\n{code}\n```"
        )
        return make_embed(description=desc, kind="info")

    # ----- Bildcaptcha erzeugen (BytesIO -> discord.File) -----
    def _make_image_captcha(self, code: str) -> discord.File:
        W, H = 320, 120
        img = Image.new("RGB", (W, H), (250, 250, 250))
        draw = ImageDraw.Draw(img)

        # leichte Verlaufs-/Noise-Fläche
        for _ in range(200):
            x1 = random.randint(0, W)
            y1 = random.randint(0, H)
            r = random.randint(10, 40)
            c = tuple(random.randint(210, 245) for _ in range(3))
            draw.ellipse((x1, y1, x1+r, y1+r), fill=c, outline=None)

        # Linien
        for _ in range(6):
            c = tuple(random.randint(120, 180) for _ in range(3))
            pts = [(random.randint(0, W), random.randint(0, H)) for __ in range(3)]
            draw.line(pts, fill=c, width=random.randint(2, 4))

        # Schrift (Fallback: default bitmap)
        try:
            font = ImageFont.truetype("arial.ttf", 48)
        except Exception:
            font = ImageFont.load_default()

        # Zeichen einzeln, random rotiert/versetzt
        spacing = W // (len(code) + 1)
        x = spacing // 2
        for ch in code:
            angle = random.uniform(-25, 25)
            scale = random.uniform(0.9, 1.2)
            color = (random.randint(20, 60), random.randint(20, 60), random.randint(20, 60))

            # einzelnes Zeichen auf separatem Layer zeichnen
            tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            tdraw = ImageDraw.Draw(tmp)
            size = int(56 * scale)
            try:
                f2 = ImageFont.truetype("arial.ttf", size)
            except Exception:
                f2 = ImageFont.load_default()

            w, h = tdraw.textlength(ch, font=f2), size
            tx = x + random.randint(-10, 10)
            ty = H//2 - h//2 + random.randint(-10, 10)
            tdraw.text((tx, ty), ch, font=f2, fill=color)

            tmp = tmp.rotate(angle, resample=Image.BICUBIC, center=(tx+w/2, ty+h/2))
            img.alpha_composite(tmp)
            x += spacing

        # mehr Rauschen
        img = img.filter(ImageFilter.SMOOTH)

        bio = io.BytesIO()
        img.convert("RGB").save(bio, format="PNG", optimize=True)
        bio.seek(0)
        return discord.File(bio, filename="captcha.png")
        

async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))