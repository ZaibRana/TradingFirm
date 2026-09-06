"""
TradingFirm — Data Engine Redis Cache Layer

Handles scan result caching, scan status tracking,
and pub/sub event publishing for inter-service communication.
"""

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

# Cache keys (matching shared/constants.py)
CACHE_LAST_SCAN = "tf:cache:last_scan"
CACHE_SCAN_STATUS = "tf:cache:scan_status"
CACHE_REFRESH_PREFIX = "tf:cache:refresh:"
CACHE_INDICATORS_PREFIX = "tf:cache:indicators:"

# Pub/sub channels
CHANNEL_SCAN_COMPLETE = "tf:scan:complete"

# TTLs (seconds)
TTL_SCAN_RESULT = 3600      # 1 hour
TTL_SCAN_STATUS = 600       # 10 minutes
TTL_REFRESH_COOLDOWN = 900  # 15 minutes
TTL_INDICATORS = 900        # 15 minutes


# ── Connection ───────────────────────────────────────────────────

async def create_redis(url: str = None) -> aioredis.Redis:
    """Create and return an async Redis connection."""
    url = url or settings.redis_url
    logger.info(f"Connecting to Redis: {url}")
    client = aioredis.from_url(url, decode_responses=True)
    # Verify connection
    await client.ping()
    logger.info("Redis connection established")
    return client


# ── Scan Result Cache ────────────────────────────────────────────

async def cache_scan_result(
    r: aioredis.Redis,
    scan_result: dict,
    ttl: int = TTL_SCAN_RESULT,
) -> None:
    """Cache the latest scan result as JSON with TTL."""
    await r.set(
        CACHE_LAST_SCAN,
        json.dumps(scan_result, default=str),
        ex=ttl,
    )
    logger.info(f"Cached scan result ({len(scan_result.get('stocks', []))} stocks, TTL={ttl}s)")


async def get_cached_scan(r: aioredis.Redis) -> Optional[dict]:
    """Retrieve cached scan result. Returns None if cache miss."""
    data = await r.get(CACHE_LAST_SCAN)
    if data is None:
        logger.debug("Cache miss: no cached scan result")
        return None
    logger.debug("Cache hit: returning cached scan result")
    return json.loads(data)


# ── Scan Status Tracking ────────────────────────────────────────

async def set_scan_status(
    r: aioredis.Redis,
    status: str,
    message: str = "",
) -> None:
    """
    Track whether a scan is currently running.
    Status: 'idle' | 'running' | 'completed' | 'failed'
    """
    payload = json.dumps({"status": status, "message": message})
    await r.set(CACHE_SCAN_STATUS, payload, ex=TTL_SCAN_STATUS)
    logger.info(f"Scan status: {status} — {message}")


async def get_scan_status(r: aioredis.Redis) -> dict:
    """Get current scan status. Defaults to 'idle' if not set."""
    data = await r.get(CACHE_SCAN_STATUS)
    if data is None:
        return {"status": "idle", "message": ""}
    return json.loads(data)


# ── Per-Ticker Refresh Cooldown ──────────────────────────────────

async def get_refresh_cooldown(r: aioredis.Redis, ticker: str) -> Optional[int]:
    """
    Seconds remaining before `ticker` may be refreshed again, or None
    if it's clear to refresh now.
    """
    ttl = await r.ttl(f"{CACHE_REFRESH_PREFIX}{ticker}")
    if ttl is None or ttl < 0:
        return None
    return ttl


async def set_refresh_cooldown(
    r: aioredis.Redis,
    ticker: str,
    ttl: int = TTL_REFRESH_COOLDOWN,
) -> None:
    """
    Start the refresh cooldown window for `ticker`. Call only after a
    successful fetch + persist — never before, so a failed refresh
    doesn't lock the ticker out with nothing to show for it.
    """
    await r.set(f"{CACHE_REFRESH_PREFIX}{ticker}", "1", ex=ttl)


# ── Per-Ticker Indicator Snapshot (Part 1.7) ─────────────────────

def indicators_key(ticker: str) -> str:
    """Cache key for one ticker's indicator snapshot. Caller passes the
    normalized ticker (main.normalize_ticker)."""
    return f"{CACHE_INDICATORS_PREFIX}{ticker}"


async def get_cached_indicators(r: aioredis.Redis, ticker: str) -> Optional[dict]:
    """
    Cached indicator body for `ticker`, or None on a miss. A stored value
    that is not valid JSON (or not a JSON object) is treated as a miss and
    logged — the caller recomputes and overwrites it.
    """
    data = await r.get(indicators_key(ticker))
    if data is None:
        return None
    try:
        body = json.loads(data)
    except (TypeError, ValueError) as e:
        logger.warning(f"Indicators cache for {ticker} is not valid JSON, ignoring: {e}")
        return None
    if not isinstance(body, dict):
        logger.warning(f"Indicators cache for {ticker} is not an object, ignoring")
        return None
    return body


async def set_cached_indicators(
    r: aioredis.Redis,
    ticker: str,
    body: dict,
    ttl: int = TTL_INDICATORS,
) -> None:
    """Cache one ticker's indicator body (JSON-ready dict) with TTL."""
    await r.set(indicators_key(ticker), json.dumps(body, default=str), ex=ttl)


async def delete_cached_indicators(r: aioredis.Redis, ticker: str) -> int:
    """Drop the cached snapshot (after a refresh wrote new bars). Returns
    the number of keys removed (0 or 1)."""
    return int(await r.delete(indicators_key(ticker)))


# ── Pub/Sub Events ───────────────────────────────────────────────

async def publish_scan_complete(
    r: aioredis.Redis,
    scan_result: dict,
) -> int:
    """
    Publish scan completion event for other services.
    Signal Engine subscribes to this to auto-generate signals.
    Returns number of subscribers that received the message.
    """
    payload = json.dumps({
        "event": "scan_complete",
        "market_status": scan_result.get("market_status", ""),
        "passed_count": scan_result.get("passed_count", 0),
        "timestamp": scan_result.get("timestamp", ""),
    }, default=str)
    receivers = await r.publish(CHANNEL_SCAN_COMPLETE, payload)
    logger.info(f"Published scan_complete event ({receivers} subscribers)")
    return receivers
