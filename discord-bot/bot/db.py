# bot/db.py
from typing import Optional
import asyncpg
from .config import settings

_pool: Optional[asyncpg.Pool] = None

async def init_db():
    """Init DB-Pool & Tabellen nur, wenn DATABASE_URL gesetzt ist."""
    global _pool
    if not settings.database_url:
        return None  # DB optional
    if _pool:
        return _pool

    _pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=5)

    async with _pool.acquire() as conn:
        # idempotente Tabellen – ergänze hier bei Bedarf
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
          guild_id        BIGINT PRIMARY KEY,
          welcome_channel BIGINT,
          welcome_role    BIGINT,
          leave_channel   BIGINT,
          default_role    BIGINT,
          lang            TEXT,
          vc_log_channel  BIGINT,                  -- <— hinzugefügt!
          templates       JSONB DEFAULT '{}'::jsonb
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS vc_overrides (
          guild_id       BIGINT    NOT NULL,
          channel_id     BIGINT    NOT NULL,
          override_roles JSONB     DEFAULT '[]'::jsonb,
          target_roles   JSONB     DEFAULT '[]'::jsonb,
          PRIMARY KEY (guild_id, channel_id)
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS vc_tracking (
          guild_id    BIGINT NOT NULL,
          channel_id  BIGINT NOT NULL,
          user_id     BIGINT NOT NULL,
          joined_at   TIMESTAMPTZ DEFAULT NOW()
        );
        """)
    return _pool

async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB not initialized. Call init_db() first (and set DATABASE_URL).")
    return _pool

async def fetchrow(*args, **kwargs):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(*args, **kwargs)

async def fetch(*args, **kwargs):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(*args, **kwargs)

async def execute(*args, **kwargs):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(*args, **kwargs)