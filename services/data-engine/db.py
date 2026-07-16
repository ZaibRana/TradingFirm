"""
TradingFirm — Data Engine Database Layer

Async PostgreSQL operations using asyncpg.
Manages connection pooling, scan result persistence,
and stock record upserts.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from config import settings

logger = logging.getLogger(__name__)


# ── Connection Pool ──────────────────────────────────────────────

async def create_db_pool() -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    dsn = settings.asyncpg_url
    logger.info(f"Connecting to database: {dsn.split('@')[1] if '@' in dsn else dsn}")
    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    logger.info("Database connection pool created")
    return pool


# ── Scan Results ─────────────────────────────────────────────────

async def save_scan_result(
    pool: asyncpg.Pool,
    scanned_at: datetime,
    market_status: str,
    total_screened: int,
    total_passed: int,
    duration_seconds: float,
    stocks: list[dict],
) -> str:
    """
    Insert a scan result into data_engine.scan_results.
    Returns the generated scan UUID.
    """
    query = """
        INSERT INTO data_engine.scan_results
            (scanned_at, market_status, total_screened, total_passed, duration_seconds, stocks)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id::text
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            query,
            scanned_at,
            market_status,
            total_screened,
            total_passed,
            duration_seconds,
            json.dumps(stocks, default=str),
        )
    scan_id = row["id"]
    logger.info(f"Saved scan result: {scan_id} ({total_passed}/{total_screened} passed)")
    return scan_id


async def get_latest_scan(pool: asyncpg.Pool) -> Optional[dict]:
    """
    Fetch the most recent scan result from the database.
    Returns dict with all fields, or None if no scans exist.
    """
    query = """
        SELECT
            id::text,
            scanned_at,
            market_status,
            total_screened,
            total_passed,
            duration_seconds,
            stocks
        FROM data_engine.scan_results
        ORDER BY scanned_at DESC
        LIMIT 1
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query)

    if row is None:
        return None

    return {
        "id": row["id"],
        "scanned_at": row["scanned_at"].isoformat(),
        "market_status": row["market_status"],
        "total_screened": row["total_screened"],
        "total_passed": row["total_passed"],
        "duration_seconds": row["duration_seconds"],
        "stocks": json.loads(row["stocks"]) if isinstance(row["stocks"], str) else row["stocks"],
    }


async def get_scan_history(pool: asyncpg.Pool, limit: int = 10) -> list[dict]:
    """
    Fetch recent scan metadata (without the full stocks array).
    """
    query = """
        SELECT
            id::text,
            scanned_at,
            market_status,
            total_screened,
            total_passed,
            duration_seconds
        FROM data_engine.scan_results
        ORDER BY scanned_at DESC
        LIMIT $1
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, limit)

    return [
        {
            "id": row["id"],
            "scanned_at": row["scanned_at"].isoformat(),
            "market_status": row["market_status"],
            "total_screened": row["total_screened"],
            "total_passed": row["total_passed"],
            "duration_seconds": row["duration_seconds"],
        }
        for row in rows
    ]


# ── Stock Records ────────────────────────────────────────────────

async def upsert_stocks(pool: asyncpg.Pool, stocks: list[dict]) -> int:
    """
    Upsert stock records into data_engine.stocks.
    Uses ON CONFLICT to update existing records.
    Returns the number of rows upserted.
    """
    if not stocks:
        return 0

    query = """
        INSERT INTO data_engine.stocks
            (ticker, name, sector, industry, market_cap, float_shares, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (ticker) DO UPDATE SET
            name = EXCLUDED.name,
            sector = EXCLUDED.sector,
            industry = EXCLUDED.industry,
            market_cap = EXCLUDED.market_cap,
            float_shares = EXCLUDED.float_shares,
            updated_at = EXCLUDED.updated_at
    """
    now = datetime.now(timezone.utc)
    records = [
        (
            s.get("symbol", s.get("ticker", "")),
            s.get("name", ""),
            s.get("sector", "Other"),
            s.get("industry", "Other"),
            s.get("market_cap", s.get("marketCap", 0)) or 0,
            s.get("float_shares", s.get("floatShares", 0)) or 0,
            now,
        )
        for s in stocks
    ]

    async with pool.acquire() as conn:
        await conn.executemany(query, records)

    logger.info(f"Upserted {len(records)} stocks into data_engine.stocks")
    return len(records)
