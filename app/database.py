"""
idrms-backend · app/database.py
Async connection pool (asyncpg) for the live API. The sync psycopg2 path
lives in scripts/ingest_mauza_census.py — ingestion is a separate, explicit
batch step, not something the API triggers on startup.
"""
from __future__ import annotations
import asyncpg
from app.config import settings

_pool: asyncpg.Pool | None = None


async def connect() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB, user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        ssl=settings.POSTGRES_SSLMODE,
        min_size=2, max_size=10,
    )


async def disconnect() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — did startup run?")
    return _pool
