"""
TradingFirm — Risk Shield Database Layer

Async PostgreSQL access using asyncpg. Part 3.1 ships the connection pool
and the error tuple only: a table helper belongs to the part that uses it
(risk.health_checks writes in 3.4, risk.macro_briefs in 3.6).

This service writes only the `risk` schema. Cross-service communication is
HTTP + Redis pub/sub, never another service's tables.
"""

import logging

import asyncpg

from config import settings

logger = logging.getLogger(__name__)

# Every exception that means "the database, not an upstream source, failed".
# Copied verbatim from data-engine (Part 2.4), including the reason OSError
# is absent: asyncio.TimeoutError *is* the builtin TimeoutError, which
# subclasses OSError, so an OSError-based tuple reports every timed-out call
# as a dead database.
DB_ERRORS = (asyncpg.PostgresError, asyncpg.InterfaceError, ConnectionError)


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
