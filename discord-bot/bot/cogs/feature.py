# bot/cogs/features.py
from __future__ import annotations

import os
import base64
import requests
from datetime import datetime, timezone
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

from ..services.features import load_features, save_features, FEATURES_FILE
from ..utils.replies import reply_text, reply_success, reply_error, make_embed, send_embed

# BOT_OWNER_ID laden (1:1: Owner-only für add_feature)
try:
    from ..settings import BOT_OWNER_ID  # wenn du es zentral definierst
except Exception:
    BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))

# Ablaufdatum des GitHub-Tokens (Format YYYY-MM-DD) – 1:1 aus deiner Env
GITHUB_TOKEN_EXPIRATION = os.getenv("GITHUB_TOKEN_EXPIRATION", "2025-11-05")  # Beispiel

def _days_until_token_expires() -> int | None:
    """Berechnet, wie viele Tage bis zum Ablauf des Tokens verbleiben (1:1)."""
    try:
        exp_date = datetime.strptime(GITHUB_TOKEN_EXPIRATION, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (exp_date - datetime.now(timezone.utc)).days
    except Exception:
        return None

async def _warn_if_token_expiring(user: discord.abc.User | discord.Member):
    """DM an Aufrufer (Owner), wenn Token in ≤7 Tagen abläuft (1:1)."""
    days_left = _days_until_token_expires()
    if days_left is not None and days_left <= 7:
        try:
            dm = await user.create_dm()
            await reply_text(
                dm,
                f"⚠️ Dein GitHub-Token läuft in **{days_left} Tagen** ab!\n"
                "Bitte erneuere es rechtzeitig in Railway.",
                kind="warning"
            )
        except Exception:
            pass  # DMs deaktiviert

def _commit_feature_file() -> tuple[bool, str]:
    """
    Committed FEATURES_FILE in dein GitHub-Repo (1:1-Logik).
    Erwartet Env: GITHUB_REPO, GITHUB_BRANCH (default main), GITHUB_TOKEN.
    """
    repo = os.getenv("GITHUB_REPO")
    branch = os.getenv("GITHUB_BRANCH", "main")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        print("❌ GitHub Commit übersprungen: Env Vars fehlen.")
        return False, "GitHub-Einstellungen fehlen"

    try:
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        # Aktuellen SHA der Datei holen
        url = f"https://api.github.com/repos/{repo}/contents/{FEATURES_FILE.name}"
        headers = {"Authorization": f"token {token}"}
        params = {"ref": branch}
        r = requests.get(url, headers=headers, params=params, timeout=20)
        if r.status_code == 401:
            return False, "GitHub-Token ungültig oder abgelaufen"
        r.raise_for_status()
        sha = r.json()["sha"]

        # Datei committen
        message = "Update features.json via bot command"
        data = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "sha": sha,
            "branch": branch
        }
        r = requests.put(url, headers=headers, json=data, timeout=20)
        r.raise_for_status()
        print("✅ features.json erfolgreich zu GitHub gepusht.")
        return True, "Features erfolgreich zu GitHub gepusht."
    except Exception as e:
        print(f"❌ GitHub Commit fehlgeschlagen: {e}")
        return False, str(e)

class FeaturesCog(commands.Cog):
    """
    /features      – zeigt die Feature-Liste (admin only, 1:1 schöne Embed-Variante)
    /add_feature   – fügt ein Feature hinzu (owner only) + GitHub-Commit + Token-Warnung
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------- /features (admin only) -------------------------

    @app_commands.command(
        name="features",
        description="Zeigt die aktuelle Feature-Liste aus features.json an."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def list_features(self, interaction: discord.Interaction):
        features = load_features()
        if not features:
            return await reply_text(interaction, "Keine Features eingetragen.", kind="info")

        # Wir packen die Liste in Embeds (Felder), inkl. Chunking gemäß Discord-Limits.
        def _new(title: str):
            return make_embed(title=title, kind="info")

        current = _new("📋 Aktuelle Features")
        field_count = 0
        total_chars = len(current.title or "")

        async def _flush():
            nonlocal current, field_count, total_chars
            if field_count == 0:
                return
            # Übersetzung der Feld-/Titeltexte übernimmt send_embed -> translate_embed
            await send_embed(interaction, current, kind="info")
            current = _new("📋 Aktuelle Features (fortgesetzt)")
            field_count = 0
            total_chars = len(current.title or "")

        for name, desc in features:
            field_name = str(name)
            field_value = (desc or "").replace("\\n", "\n")

            # >1024 Zeichen splitten
            parts: List[str] = [field_value[i:i+1024] for i in range(0, len(field_value), 1024)] or ["—"]

            for idx, part in enumerate(parts):
                n = field_name if idx == 0 else f"{field_name} (…)"
                projected = total_chars + len(n) + len(part)
                if field_count >= 24 or projected >= 5800:
                    await _flush()
                current.add_field(name=n, value=part, inline=False)
                field_count += 1
                total_chars += len(n) + len(part)

        await _flush()

    # -------------------- /add_feature (owner only + GitHub) ------------------

    @app_commands.command(
        name="add_feature",
        description="Fügt ein neues Feature (Name + Beschreibung) hinzu und pusht optional zu GitHub."
    )
    @app_commands.describe(
        name="Name des Features",
        description="Beschreibung (\\n für Zeilenumbrüche erlaubt)"
    )
    async def add_feature(self, interaction: discord.Interaction, name: str, description: str):
        # Owner-only Check (1:1)
        if interaction.user.id != BOT_OWNER_ID:
            return await reply_error(interaction, "❌ Du darfst diesen Befehl nicht nutzen.")

        features = load_features()
        if any((f[0] or "").lower() == (name or "").lower() for f in features):
            return await reply_text(interaction, f"⚠️ Feature `{name}` existiert bereits.", kind="warning")

        # Neues Feature anhängen & speichern
        features.append([name, description])
        save_features(features)

        # In GitHub committen
        success, message = _commit_feature_file()
        if success:
            await reply_success(interaction, f"✅ Feature `{name}` hinzugefügt.\n📤 {message}")
        else:
            await reply_text(
                interaction,
                f"⚠️ Feature `{name}` wurde lokal gespeichert, aber nicht zu GitHub gepusht.\nGrund: {message}",
                kind="warning"
            )

        # Warnung bei bald ablaufendem Token (DM an Aufrufer/Owner)
        await _warn_if_token_expiring(interaction.user)

# bot/cogs/features.py (erweitert)
# ... [bestehende Imports + Hilfsfunktionen bleiben unverändert] ...

class FeaturesCog(commands.Cog):
    """
    /features         – zeigt die Feature-Liste (admin only)
    /add_feature      – fügt ein Feature hinzu (owner only) + GitHub-Commit + Token-Warnung
    /remove_feature   – entfernt ein Feature (owner only)
    /update_feature   – ändert die Beschreibung eines Features (owner only)
    /reload_features  – lädt features.json neu (owner only)
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------- /features (admin only) -------------------------
    # ... [bestehendes list_features bleibt unverändert] ...

    # -------------------- /add_feature (owner only + GitHub) ------------------
    # ... [bestehendes add_feature bleibt unverändert] ...

    # ------------------- /remove_feature (owner only + GitHub) ----------------
    @app_commands.command(
        name="remove_feature",
        description="Entfernt ein bestehendes Feature aus der Liste."
    )
    @app_commands.describe(
        name="Name des Features, das entfernt werden soll"
    )
    async def remove_feature(self, interaction: discord.Interaction, name: str):
        if interaction.user.id != BOT_OWNER_ID:
            return await reply_error(interaction, "❌ Du darfst diesen Befehl nicht nutzen.")

        features = load_features()
        idx = next((i for i, f in enumerate(features) if (f[0] or "").lower() == name.lower()), None)
        if idx is None:
            return await reply_error(interaction, f"❌ Feature `{name}` wurde nicht gefunden.")

        removed = features.pop(idx)
        save_features(features)

        success, message = _commit_feature_file()
        if success:
            await reply_success(interaction, f"✅ Feature `{removed[0]}` entfernt.\n📤 {message}")
        else:
            await reply_text(interaction, f"⚠️ Entfernt, aber nicht zu GitHub gepusht.\nGrund: {message}", kind="warning")

        await _warn_if_token_expiring(interaction.user)

    # ------------------- /update_feature (owner only + GitHub) ----------------
    @app_commands.command(
        name="update_feature",
        description="Aktualisiert die Beschreibung eines bestehenden Features."
    )
    @app_commands.describe(
        name="Name des Features",
        description="Neue Beschreibung (\\n für Zeilenumbrüche erlaubt)"
    )
    async def update_feature(self, interaction: discord.Interaction, name: str, description: str):
        if interaction.user.id != BOT_OWNER_ID:
            return await reply_error(interaction, "❌ Du darfst diesen Befehl nicht nutzen.")

        features = load_features()
        idx = next((i for i, f in enumerate(features) if (f[0] or "").lower() == name.lower()), None)
        if idx is None:
            return await reply_error(interaction, f"❌ Feature `{name}` wurde nicht gefunden.")

        features[idx][1] = description
        save_features(features)

        success, message = _commit_feature_file()
        if success:
            await reply_success(interaction, f"✅ Feature `{name}` aktualisiert.\n📤 {message}")
        else:
            await reply_text(interaction, f"⚠️ Aktualisiert, aber nicht zu GitHub gepusht.\nGrund: {message}", kind="warning")

        await _warn_if_token_expiring(interaction.user)

    # -------------------- /reload_features (owner only) -----------------------
    @app_commands.command(
        name="reload_features",
        description="Lädt features.json neu (falls extern geändert)."
    )
    async def reload_features(self, interaction: discord.Interaction):
        if interaction.user.id != BOT_OWNER_ID:
            return await reply_error(interaction, "❌ Du darfst diesen Befehl nicht nutzen.")

        try:
            _ = load_features()
            await reply_success(interaction, "🔄 Features neu geladen.")
        except Exception as e:
            await reply_error(interaction, f"❌ Fehler beim Neuladen: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(FeaturesCog(bot))
    # Auto-sync der Slash-Commands (global)
    try:
        synced = await bot.tree.sync()
        print(f"[features] Slash-Commands synchronisiert ({len(synced)} Kommandos).")
    except Exception as e:
        print(f"[features] Slash-Command-Sync fehlgeschlagen: {e}")