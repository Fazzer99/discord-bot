# bot/cogs/verify.py
from __future__ import annotations
import asyncio
import random
import string
from dataclasses import dataclass
from typing import Optional, Dict

import discord
from discord import app_commands
from discord.ext import commands

from ..utils.checks import require_manage_guild
from ..utils.replies import reply_text, reply_error, reply_success, make_embed
from ..services.guild_config import get_guild_cfg, update_guild_cfg

# ------------------------------- Captcha core -------------------------------

_CAPTCHA_LEN = 6
_ALPHABET = string.ascii_uppercase + string.digits  # A–Z + 0–9 (ohne lookalikes? optional)

def _make_code(n: int = _CAPTCHA_LEN) -> str:
    # Optional: you could remove ambiguous chars like 0/O, 1/I/L by filtering _ALPHABET
    return "".join(random.choice(_ALPHABET) for _ in range(n))

@dataclass
class CaptchaSession:
    code: str
    attempts_left: int
    locked_until: float  # epoch seconds; 0 == not locked

# pro Guild speichern wir ephemere Sessions (User->Session)
_sessions: Dict[int, Dict[int, CaptchaSession]] = {}   # guild_id -> { user_id: CaptchaSession }


# ----------------------------- UI: Modal & View -----------------------------

class CaptchaModal(discord.ui.Modal, title="Captcha Answer"):
    answer = discord.ui.TextInput(label="Answer", placeholder="Type the code shown in the panel", required=True, max_length=32)

    def __init__(self, cog: "VerifyCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        # Safety: defer if needed (um "Anwendung reagiert nicht" zu vermeiden)
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await reply_error(interaction, "❌ This only works in a server.", ephemeral=True)

        # Owner brauchen keine Verifizierung
        if interaction.user.id == guild.owner_id:
            return await reply_error(interaction, "ℹ️ Du bist der Server-Owner und brauchst keine Verifizierung.", ephemeral=True)

        cfg = await get_guild_cfg(guild.id)
        role_id  = cfg.get("verify_role_id")
        attempts = int(cfg.get("verify_attempts") or 3)
        if not role_id:
            return await reply_error(interaction, "❌ Verify-Rolle ist noch nicht konfiguriert. Admin: `/set_verify`.", ephemeral=True)

        sessmap = _sessions.setdefault(guild.id, {})
        now = asyncio.get_event_loop().time()
        sess = sessmap.get(interaction.user.id)

        # Session vorhanden?
        if not sess:
            sess = CaptchaSession(code=_make_code(), attempts_left=attempts, locked_until=0.0)
            sessmap[interaction.user.id] = sess

        # Lock-Prüfung
        if sess.locked_until and now < sess.locked_until:
            wait_s = int(sess.locked_until - now)
            return await reply_error(interaction, f"⏳ Zu viele Fehlversuche. Bitte in **{wait_s}** Sek. erneut versuchen.", ephemeral=True)

        value = str(self.answer.value or "").strip().upper()
        if value != sess.code.upper():
            sess.attempts_left -= 1
            if sess.attempts_left <= 0:
                # 2-Minuten Cooldown nach X Fehlversuchen
                sess.locked_until = now + 120
                sess.attempts_left = attempts
                # Neues Rätsel beim nächsten Mal
                sess.code = _make_code()
                return await reply_error(
                    interaction, "❌ Falsche Antwort. Du wurdest **für 2 Minuten gesperrt**. Versuche es danach erneut.",
                    ephemeral=True
                )
            else:
                return await reply_error(
                    interaction, f"❌ Falsch. Verbleibende Versuche: **{sess.attempts_left}**.",
                    ephemeral=True
                )

        # ✅ Erfolg
        role = guild.get_role(role_id)
        if not role:
            return await reply_error(interaction, "❌ Verify-Rolle existiert nicht (wurde evtl. gelöscht). Admin: `/set_verify`.", ephemeral=True)

        try:
            await interaction.user.add_roles(role, reason="Ignix Verify passed")
        except discord.Forbidden:
            return await reply_error(interaction, "❌ Mir fehlt die Berechtigung, die Rolle zu vergeben.", ephemeral=True)

        # Session schließen
        sessmap.pop(interaction.user.id, None)

        # Logging
        log_id = cfg.get("verify_log_channel")
        if log_id:
            ch = guild.get_channel(log_id)
            if isinstance(ch, discord.TextChannel):
                try:
                    emb = make_embed(
                        title="✅ Verification passed",
                        description=f"{interaction.user.mention} hat die Verifizierung bestanden.",
                        kind="success"
                    )
                    await ch.send(embed=emb)
                except Exception:
                    pass

        return await reply_success(interaction, "✅ Verifizierung erfolgreich. Willkommen!", ephemeral=True)


class VerifyPanel(discord.ui.View):
    def __init__(self, cog: "VerifyCog"):
        super().__init__(timeout=None)  # persistent
        self.cog = cog

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="ignix:verify")
    async def btn_verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=False)

        guild = interaction.guild
        if guild and interaction.user.id == guild.owner_id:
            return await reply_text(interaction, "ℹ️ Du bist der Server-Owner und musst dich nicht verifizieren.", kind="info", ephemeral=True)

        # neue/fortgesetzte Session anzeigen
        cfg = await get_guild_cfg(interaction.guild.id)
        sess = _sessions.setdefault(interaction.guild.id, {}).get(interaction.user.id)
        code = sess.code if sess else _make_code()
        if not sess:
            _sessions[interaction.guild.id][interaction.user.id] = CaptchaSession(
                code=code, attempts_left=int(cfg.get("verify_attempts") or 3), locked_until=0.0
            )

        # Kurzen Hinweis + Modal öffnen
        hint = make_embed(
            title="🧩 Are you human?",
            description=(
                "Bitte **diesen Code** exakt eingeben (Groß/Klein egal):\n"
                f"`{code}`\n\n"
                "• Zeichne *keine* Linien – einfach nur den Code abtippen.\n"
                "• Du hast begrenzte Versuche; bei zu vielen Fehlversuchen kurzer Timeout."
            ),
            kind="info"
        )
        try:
            await interaction.followup.send(embed=hint, ephemeral=True)
        except Exception:
            pass

        await interaction.followup.send_modal(CaptchaModal(self.cog))

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, custom_id="ignix:verify_help")
    async def btn_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=False)

        cfg = await get_guild_cfg(interaction.guild.id)
        role_id = cfg.get("verify_role_id")
        attempts = int(cfg.get("verify_attempts") or 3)
        role_txt = f"<@&{role_id}>" if role_id else "—"

        emb = make_embed(
            title="ℹ️ Verification Help",
            description=(
                "Klicke **Verify**, lies den Code und gib ihn im Modal ein.\n"
                f"• Max. Versuche: **{attempts}**\n"
                "• Bei zu vielen Fehlversuchen wirst du kurzzeitig gesperrt.\n"
                f"• Erfolgreich? Du erhältst die Rolle {role_txt}."
            ),
            kind="info"
        )
        await interaction.followup.send(embed=emb, ephemeral=True)


# ---------------------------------- Cog ------------------------------------

class VerifyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register persistent view so buttons keep working across restarts
        self.bot.add_view(VerifyPanel(self))

    # ------- Admin: Setup & Status -------

    @app_commands.command(name="set_verify", description="Richtet die Verify-Funktion ein und postet ein Panel.")
    @require_manage_guild()
    @app_commands.describe(
        role="Rolle, die nach erfolgreicher Verifizierung vergeben wird",
        channel="Kanal, in dem das Verify-Panel gepostet werden soll",
        log_channel="(Optional) Kanal für Verify-Logs",
        attempts="(Optional) Erlaubte Fehlversuche (Standard 3)"
    )
    async def set_verify(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        channel: discord.TextChannel,
        log_channel: Optional[discord.TextChannel] = None,
        attempts: Optional[int] = 3,
    ):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        gid = interaction.guild.id
        data = {
            "verify_role_id": role.id,
            "verify_channel_id": channel.id,
            "verify_attempts": max(1, int(attempts or 3)),
        }
        if log_channel:
            data["verify_log_channel"] = log_channel.id

        await update_guild_cfg(gid, **data)

        # Verify-Panel posten
        view = VerifyPanel(self)
        emb = make_embed(
            title="Verification Required!",
            description=(
                "Um Zugang zum Server zu bekommen, musst du die Verifizierung bestehen.\n"
                "Klicke auf **Verify** und folge den Anweisungen."
            ),
            kind="info"
        )
        msg = await channel.send(embed=emb, view=view)
        await update_guild_cfg(gid, verify_panel_message_id=msg.id)

        return await reply_success(
            interaction,
            f"✅ Verify eingerichtet. Panel gepostet in {channel.mention}. Rolle: {role.mention}.",
            ephemeral=True
        )

    @app_commands.command(name="verify_status", description="Zeigt die aktuelle Verify-Konfiguration.")
    @require_manage_guild()
    async def verify_status(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        cfg = await get_guild_cfg(interaction.guild.id)
        r = cfg.get("verify_role_id")
        c = cfg.get("verify_channel_id")
        l = cfg.get("verify_log_channel")
        a = int(cfg.get("verify_attempts") or 3)
        m = cfg.get("verify_panel_message_id")

        desc = (
            f"**Role:** {('<@&'+str(r)+'>') if r else '—'}\n"
            f"**Panel-Channel:** {('<#'+str(c)+'>') if c else '—'}\n"
            f"**Log-Channel:** {('<#'+str(l)+'>') if l else '—'}\n"
            f"**Attempts:** {a}\n"
            f"**Panel-Message ID:** {m or '—'}"
        )
        emb = make_embed(title="🔧 Verify – Konfiguration", description=desc, kind="info")
        return await interaction.followup.send(embed=emb, ephemeral=True)

    @app_commands.command(name="verify_post", description="Postet das Verify-Panel erneut in den konfigurierten Kanal.")
    @require_manage_guild()
    async def verify_post(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        cfg = await get_guild_cfg(interaction.guild.id)
        ch_id = cfg.get("verify_channel_id")
        if not ch_id:
            return await reply_error(interaction, "❌ Kein Verify-Channel gesetzt. Nutze `/set_verify`.", ephemeral=True)
        ch = interaction.guild.get_channel(ch_id)
        if not isinstance(ch, discord.TextChannel):
            return await reply_error(interaction, "❌ Verify-Channel ist ungültig.", ephemeral=True)

        view = VerifyPanel(self)
        emb = make_embed(
            title="Verification Required!",
            description="Klicke **Verify** und folge den Anweisungen.",
            kind="info"
        )
        msg = await ch.send(embed=emb, view=view)
        await update_guild_cfg(interaction.guild.id, verify_panel_message_id=msg.id)
        return await reply_success(interaction, f"✅ Panel erneut gepostet in {ch.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))