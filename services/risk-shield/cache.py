"""
TradingFirm — Risk Shield Redis Cache Layer

Key namespace, TTLs, the fail-open read-through cache and the health
pub/sub channel for Phase 3.

The pattern is data-engine's `cache.py` (Part 3.1 copies it deliberately —
separate images, no shared package), with one difference that matters: every
key here lives under `tf:risk:`, never `tf:cache:`, which data-engine owns.
Both services share Redis DB 0 in prod.
"""

import json
import logging
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as aioredis

from config import settings

logger = logging.getLogger(__name__)

# ── Namespace ────────────────────────────────────────────────────

# Everything this service writes starts here. data-engine owns tf:cache:*.
RISK_PREFIX = "tf:risk:"
CACHE_PREFIX = f"{RISK_PREFIX}cache:"

# Pub/sub channel for regime changes (plan 3.4). Named here so 3.4 does not
# invent a second spelling.
CHANNEL_HEALTH = f"{RISK_PREFIX}health"

# Cache kinds, shipped now and used by their part.
KIND_QUOTES = "quotes"   # 3.2: the batched core-ticker download
KIND_FRED = "fred"       # 3.2: one FRED series
KIND_HEALTH = "health"   # 3.4: the latest health snapshot

# TTLs (seconds)
TTL_QUOTES = 300     # 5 min (plan 3.2)
TTL_FRED = 21600     # 6 hours (plan 3.2)
TTL_HEALTH = 300     # 5 min, the scheduler's market-hours cadence (3.4)


# ── Keys ─────────────────────────────────────────────────────────

def canonical(name: str) -> str:
    """
    The one normalization every Phase 3 key goes through (G1.5): tickers
    (`spy` → `SPY`) and FRED series ids (` dgs10 ` → `DGS10`) are the same
    kind of name, so they get the same treatment and cannot produce two
    keys for one thing.
    """
    if not isinstance(name, str):
        raise ValueError(f"key name must be a string, got {type(name).__name__}")
    return name.strip().upper()


def risk_key(kind: str, name: str = "") -> str:
    """
    Cache key for one cached thing: `tf:risk:cache:{kind}`, or
    `tf:risk:cache:{kind}:{name}` when the thing is per-name. `kind` is a
    module constant, `name` goes through canonical().
    """
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("cache key kind must be a non-empty string")
    kind = kind.strip().lower()
    if not name:
        return f"{CACHE_PREFIX}{kind}"
    return f"{CACHE_PREFIX}{kind}:{canonical(name)}"


# ── Connection ───────────────────────────────────────────────────

async def create_redis(url: str = None, timeout: float = None) -> aioredis.Redis:
    """
    Create and return an async Redis connection, verified with a PING.

    `timeout` bounds the socket connect and each socket read (redis-py's
    defaults are unbounded). The lifespan additionally wraps this whole
    call in asyncio.wait_for — the hard bound (Part 3.1 decision 6).
    """
    import config

    if timeout is None:
        timeout = config.STARTUP_TIMEOUT
    url = url or settings.redis_url
    logger.info(f"Connecting to Redis: {url}")
    client = aioredis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    await client.ping()
    logger.info("Redis connection established")
    return client


# ── Generic JSON cache ───────────────────────────────────────────

async def get_cached_json(r: aioredis.Redis, key: str) -> Optional[Any]:
    """Decoded JSON at `key`, or None on a miss. A stored value that is not
    valid JSON is treated as a miss and logged (the caller recomputes and
    overwrites it)."""
    data = await r.get(key)
    if data is None:
        return None
    try:
        return json.loads(data)
    except (TypeError, ValueError) as e:
        logger.warning(f"Cache at {key} is not valid JSON, ignoring: {e}")
        return None


async def set_cached_json(r: aioredis.Redis, key: str, body: Any, ttl: int) -> None:
    """Cache any JSON-serializable body at `key` with TTL."""
    await r.set(key, json.dumps(body, default=str), ex=ttl)


async def cached_json(
    r: Optional[aioredis.Redis],
    key: str,
    ttl: int,
    fetch: Callable[[], Awaitable[Any]],
    *,
    valid: Optional[Callable[[Any], bool]] = None,
) -> tuple[Any, bool]:
    """
    Read-through cache: return (body, from_cache). On a miss, `fetch()`
    runs and its result is cached for `ttl` seconds. A cached body that
    fails `valid` (wrong shape) is a miss too: logged, refetched,
    overwritten. Fail-open on Redis — `r` may be None, and a raise on get
    or set is logged and ignored so the caller still gets a body. A raise
    inside `fetch()` propagates and nothing is cached.

    An empty body ([] or {}) is an answer, not a miss: it is cached and
    returned as-is. Only a literal absence is a miss.
    """
    if r is not None:
        try:
            body = await get_cached_json(r, key)
        except Exception as e:
            logger.warning(f"Cache read failed for {key}: {e}")
            body = None
        if body is not None and valid is not None and not valid(body):
            logger.warning(f"Cache at {key} has the wrong shape, ignoring")
            body = None
        if body is not None:
            return body, True

    body = await fetch()

    if r is not None:
        try:
            await set_cached_json(r, key, body, ttl)
        except Exception as e:
            logger.warning(f"Cache write failed for {key}: {e}")
    return body, False
