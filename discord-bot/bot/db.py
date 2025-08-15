# bot/db.py
from typing import Optional
import asyncpg
from .config import settings

_pool: Optional[asyncpg.Pool] = None


async def init_db():
    """
    Initialisiert den DB-Pool (falls DATABASE_URL gesetzt) und legt idempotent
    die benötigten Tabellen im public-Schema an. Erzwingt außerdem die
    Simple-Tracking-Struktur für vc_tracking (nur guild_id + channel_id).
    """
    global _pool
    if not settings.database_url:
        # DB ist optional – einfach nichts tun, wenn keine URL gesetzt ist
        return None
    if _pool is not None:
        return _pool

    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=5,
    )

    async with _pool.acquire() as conn:
        # Sicherstellen, dass wir im public-Schema arbeiten (gegen Shadowing)
        await conn.execute("SET search_path TO public;")

        # --- guild_settings ---------------------------------------------------
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS public.guild_settings (
          guild_id        BIGINT PRIMARY KEY,
          welcome_channel BIGINT,
          welcome_role    BIGINT,
          leave_channel   BIGINT,
          default_role    BIGINT,
          lang            TEXT,
          vc_log_channel  BIGINT,
          templates       JSONB DEFAULT '{}'::jsonb
        );
        """)

        # --- vc_overrides -----------------------------------------------------
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS public.vc_overrides (
          guild_id       BIGINT    NOT NULL,
          channel_id     BIGINT    NOT NULL,
          override_roles JSONB     DEFAULT '[]'::jsonb,
          target_roles   JSONB     DEFAULT '[]'::jsonb,
          PRIMARY KEY (guild_id, channel_id)
        );
        """)

        # --- vc_tracking (Simple Tracking: NUR guild_id + channel_id) --------
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS public.vc_tracking (
          guild_id   BIGINT NOT NULL,
          channel_id BIGINT NOT NULL,
          PRIMARY KEY (guild_id, channel_id)
        );
        """)

        # Falls aus alten Versionen noch Spalten existieren -> entfernen
        # (idempotent; macht nichts, wenn die Spalten nicht vorhanden sind)
        await conn.execute("""
        ALTER TABLE public.vc_tracking
          DROP COLUMN IF EXISTS user_id,
          DROP COLUMN IF EXISTS joined_at;
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