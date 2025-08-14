from __future__ import annotations
from typing import Any, Dict
from ..db import fetchrow, execute

async def get_guild_cfg(guild_id: int) -> Dict[str, Any]:
    row = await fetchrow("SELECT * FROM guild_settings WHERE guild_id = $1", guild_id)
    return dict(row) if row else {}

async def update_guild_cfg(guild_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields.keys(), start=2))
    values = list(fields.values())
    await execute(f"UPDATE guild_settings SET {cols} WHERE guild_id = $1", guild_id, *values)
