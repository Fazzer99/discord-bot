# bot/services/guild_config.py
from __future__ import annotations
import json
from ..db import fetchrow, execute  # <— aus bot/db.py importieren

async def get_guild_cfg(guild_id: int) -> dict:
    """
    Lädt (und initialisiert bei Bedarf) die Settings einer Guild.
    Garantiert:
      - templates: dict
      - default_role: None|int
    """
    row = await fetchrow("SELECT * FROM guild_settings WHERE guild_id=$1", guild_id)
    if row:
        d = dict(row)

        # templates als dict sicherstellen
        tmpl = d.get("templates")
        if isinstance(tmpl, str):
            try:
                d["templates"] = json.loads(tmpl)
            except json.JSONDecodeError:
                d["templates"] = {}
        elif tmpl is None:
            d["templates"] = {}

        d.setdefault("default_role", None)
        return d

    # noch nicht vorhanden → anlegen
    await execute("INSERT INTO guild_settings (guild_id) VALUES ($1)", guild_id)
    return await get_guild_cfg(guild_id)

async def update_guild_cfg(guild_id: int, **fields):
    """
    Schreibt Felder zurück (JSONB bei dict/list wird als JSON gespeichert).
    """
    if not fields:
        return
    cols = ", ".join(f"{col} = ${i+2}" for i, col in enumerate(fields))
    vals = [guild_id]
    for v in fields.values():
        if isinstance(v, (dict, list)):
            vals.append(json.dumps(v))
        else:
            vals.append(v)
    await execute(f"UPDATE guild_settings SET {cols} WHERE guild_id=$1", *vals)