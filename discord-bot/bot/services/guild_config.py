# bot/services/guild_config.py
from __future__ import annotations
import json
from typing import Dict, Any
from ..db import fetchrow, execute

# Diese Legacy-Spalten bleiben wie gehabt in einzelnen DB-Spalten
LEGACY_COLS = {
    "welcome_channel",
    "welcome_role",
    "leave_channel",
    "templates",
    "default_role",
    "vc_log_channel",
    "lang",
    "tz",
}

SELECT_COLS = (
    "guild_id, welcome_channel, welcome_role, leave_channel, "
    "templates, default_role, vc_log_channel, lang, tz, settings"
)

async def get_guild_cfg(guild_id: int) -> dict:
    """
    Lädt (und initialisiert bei Bedarf) die Guild-Konfiguration.
    - Legacy-Felder bleiben als Top-Level-Keys (kompatibel zu deinem bestehenden Code).
    - Neue/zusätzliche Dinge liegen in cfg['settings'] (jsonb).
    """
    row = await fetchrow(f"SELECT {SELECT_COLS} FROM guild_settings WHERE guild_id=$1", guild_id)
    if not row:
        # neu anlegen mit leeren defaults
        await execute(
            "INSERT INTO guild_settings (guild_id, settings) VALUES ($1, $2)",
            guild_id, json.dumps({})
        )
        row = await fetchrow(f"SELECT {SELECT_COLS} FROM guild_settings WHERE guild_id=$1", guild_id)

    data = dict(row)

    # templates zuverlässig zu dict machen
    t = data.get("templates")
    if isinstance(t, str):
        try:
            data["templates"] = json.loads(t)
        except Exception:
            data["templates"] = {}
    elif t is None:
        data["templates"] = {}
    elif not isinstance(t, dict):
        data["templates"] = {}

    # settings zuverlässig zu dict machen
    s = data.get("settings")
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except Exception:
            s = {}
    if s is None or not isinstance(s, dict):
        s = {}
    data["settings"] = s

    # sinnvolle Defaults für Legacy-Felder
    data.setdefault("default_role", None)
    data.setdefault("lang", "en")
    data.setdefault("tz", 0)  # Minuten-Offset zu UTC (dein neues Modell)

    return data


async def update_guild_cfg(guild_id: int, **fields: Any):
    """
    Aktualisiert gezielt Felder:
    - Keys, die in LEGACY_COLS sind -> direkte Spaltenupdates.
    - Alle anderen Keys -> werden in settings[key] abgelegt.
    - Spezielle Behandlung: wenn 'settings' selbst mitgegeben wird (dict), wird es gemerged.
    """
    if not fields:
        return

    cfg = await get_guild_cfg(guild_id)
    current_settings: Dict[str, Any] = dict(cfg.get("settings") or {})

    # 1) Felder aufteilen
    legacy_updates: Dict[str, Any] = {}
    settings_updates: Dict[str, Any] = {}

    for k, v in fields.items():
        if k == "settings" and isinstance(v, dict):
            # Deep-merge auf Top-Level der settings
            settings_updates.update(v)
        elif k in LEGACY_COLS:
            legacy_updates[k] = v
        else:
            # Unbekannte Keys -> unter settings speichern
            settings_updates[k] = v

    # 2) Settings mergen
    if settings_updates:
        for k, v in settings_updates.items():
            current_settings[k] = v

    # 3) SQL zusammenbauen
    set_parts = []
    values = [guild_id]
    idx = 2

    # Legacy-Spalten setzen (dict/list als JSON-Text speichern wie bisher)
    for col, val in legacy_updates.items():
        set_parts.append(f"{col} = ${idx}")
        if isinstance(val, (dict, list)):
            values.append(json.dumps(val))
        else:
            values.append(val)
        idx += 1

    # settings (jsonb) setzen
    if settings_updates:
        set_parts.append(f"settings = ${idx}")
        values.append(json.dumps(current_settings))
        idx += 1

    if not set_parts:
        return  # nichts zu tun

    sql = f"UPDATE guild_settings SET {', '.join(set_parts)} WHERE guild_id=$1"
    await execute(sql, *values)