"""
TradingFirm — Risk Shield Database Layer

Async PostgreSQL access using asyncpg. Part 3.1 ships the connection pool
and the error tuple only: a table helper belongs to the part that uses it
(risk.health_checks writes in 3.4, risk.macro_briefs in 3.6).

This service writes only the `risk` schema. Cross-service communication is
HTTP + Redis pub/sub, never another service's tables.
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional

import asyncpg

from config import settings

logger = logging.getLogger(__name__)

# Every exception that means "the database, not an upstream source, failed".
# Copied verbatim from data-engine (Part 2.4), including the reason OSError
# is absent: asyncio.TimeoutError *is* the builtin TimeoutError, which
# subclasses OSError, so an OSError-based tuple reports every timed-out call
# as a dead database.
DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, ConnectionError)

# "The database did not answer", timeouts included (Part 3.4). asyncpg's
# command_timeout raises TimeoutError, which DB_ERRORS leaves out on purpose
# (above). The health-check writes and the /market endpoints catch this one.
DB_FAILURES = (*DB_ERRORS, TimeoutError)


def _safe_dsn(dsn: str) -> str:
    """The DSN with the credentials half removed, for logging (G14)."""
    return dsn.split("@")[1] if "@" in dsn else dsn


async def create_db_pool(timeout: float = None) -> asyncpg.Pool:
    """
    Create and return an asyncpg connection pool.

    `timeout` bounds establishing each connection (asyncpg's default is
    60 s). The pool opens `min_size` connections, so this bound alone
    allows ~2x — the lifespan's asyncio.wait_for is the hard one
    (Part 3.1 decision 6).
    """
    import config

    if timeout is None:
        timeout = config.STARTUP_TIMEOUT
    dsn = settings.asyncpg_url
    logger.info(f"Connecting to database: {_safe_dsn(dsn)}")
    pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
        timeout=timeout,
    )
    logger.info("Database connection pool created")
    return pool


# ── risk.health_checks (Part 3.4, spec decision 6) ───────────────
# The table exists since 001. `kind` ("market" | "settle") lives in the
# indicators JSONB, not a column, so 3.4 needs no migration.

INSERT_HEALTH_CHECK_SQL = """
INSERT INTO risk.health_checks (checked_at, score, regime, trend, indicators)
VALUES ($1, $2, $3, $4, $5::jsonb)
"""

LATEST_HEALTH_CHECK_SQL = """
SELECT checked_at, score, regime, trend, indicators
FROM risk.health_checks
ORDER BY checked_at DESC
LIMIT 1
"""

LATEST_SCORED_HEALTH_CHECK_SQL = """
SELECT checked_at, score, regime
FROM risk.health_checks
WHERE score IS NOT NULL
ORDER BY checked_at DESC
LIMIT 1
"""

# The trend base: the latest scored settle before a cutoff (the session open
# of the check's date), so even the 16:20 check reads an earlier session.
SETTLE_BASE_SQL = """
SELECT checked_at, score
FROM risk.health_checks
WHERE indicators->>'kind' = 'settle' AND score IS NOT NULL AND checked_at < $1
ORDER BY checked_at DESC
LIMIT 1
"""

# Two fields out of the JSONB as text, never the whole blob (~4 KB a row).
HEALTH_HISTORY_SQL = """
SELECT checked_at, score, regime, trend,
       indicators->>'kind' AS kind, indicators->>'stale' AS stale
FROM risk.health_checks
WHERE checked_at >= $1
ORDER BY checked_at ASC
"""


def health_indicators(health: dict, kind: str, settle: Optional[dict]) -> str:
    """The indicators JSONB for one check. allow_nan=False: a NaN from a
    monitor bug raises ValueError here, before any SQL."""
    return json.dumps({
        "kind": kind,
        "coverage": health.get("coverage"),
        "stale": bool(health.get("stale")),
        "staleMonitors": health.get("staleMonitors") or [],
        "monitors": health.get("monitors") or {},
        "inputs": health.get("inputs") or {},
        "settleScore": settle["score"] if settle else None,
        "settleCheckedAt": settle["checkedAt"].isoformat() if settle else None,
    }, allow_nan=False)


async def insert_health_check(pool, health: dict, kind: str, trend: Optional[str],
                              settle: Optional[dict]) -> None:
    """One row per check, null scores included. checked_at is the snapshot's
    own checkedAt, never DEFAULT now()."""
    indicators = health_indicators(health, kind, settle)
    await pool.execute(
        INSERT_HEALTH_CHECK_SQL,
        datetime.fromisoformat(health["checkedAt"]),
        health["score"],
        health["regime"],
        trend,
        indicators,
    )


async def latest_health_check(pool) -> Optional[dict]:
    row = await pool.fetchrow(LATEST_HEALTH_CHECK_SQL)
    return dict(row) if row is not None else None


async def latest_scored_health_check(pool) -> Optional[dict]:
    row = await pool.fetchrow(LATEST_SCORED_HEALTH_CHECK_SQL)
    return dict(row) if row is not None else None


async def settle_base(pool, before: datetime) -> Optional[dict]:
    """{score, checkedAt} of the latest scored settle before `before`, or None."""
    row = await pool.fetchrow(SETTLE_BASE_SQL, before)
    if row is None:
        return None
    return {"score": row["score"], "checkedAt": row["checked_at"]}


def _json_bool(text: Any) -> Optional[bool]:
    return {"true": True, "false": False}.get(text)


async def health_history(pool, since: datetime) -> list[dict]:
    """Rows since `since`, ascending, without the indicators blob. A stale
    value that is not a JSON boolean reads as None, never an error."""
    rows = await pool.fetch(HEALTH_HISTORY_SQL, since)
    return [
        {
            "checked_at": row["checked_at"],
            "score": row["score"],
            "regime": row["regime"],
            "trend": row["trend"],
            "kind": row["kind"],
            "stale": _json_bool(row["stale"]),
        }
        for row in rows
    ]
