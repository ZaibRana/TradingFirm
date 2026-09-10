"""
TradingFirm — Risk Shield (Service 3)

Responsibilities:
  - Market health scoring (VIX, breadth, sector rotation, etc.)
  - Regime detection (HEALTHY / CAUTIOUS / DANGER / CRITICAL)
  - Crash guard alerts
  - Health check scheduling (every 5 min during market hours)

Endpoints:
  GET /health             — service health: dependency state at boot, scheduler state
  GET /                   — service info
  GET /market/health      — the latest health check (Postgres only, Part 3.4)
  GET /market/indicators  — the six monitors of the latest check
  GET /market/history     — checks over the last ?days=1..90 (default 30)

Port: 8003
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

import config
import db
from config import settings

# ── Logging ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("risk-shield")


# ── Lifespan (startup/shutdown) ──────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize Redis and the DB pool on startup, close them on shutdown.

    Fail-open and bounded (Part 3.1 decision 6): each attempt gets
    config.STARTUP_TIMEOUT seconds, a failure or a timeout logs a warning
    and leaves the dependency as None, and the service still boots and
    serves /health. Worst-case boot is two timeouts, ~10 s.

    config.STARTUP_TIMEOUT is read here at call time, never bound at
    import: the lifespan tests monkeypatch it down to keep the slow paths
    fast, and a from-import would copy the value past the patch.
    """
    logger.info("Starting Risk Shield...")

    # Redis (optional). The factory and its PING are bounded together: a
    # Redis that accepts the socket and then never answers is as bad as one
    # that never accepts it.
    try:
        from cache import create_redis
        app.state.redis = await asyncio.wait_for(
            create_redis(), timeout=config.STARTUP_TIMEOUT
        )
        logger.info("✅ Redis connection ready")
    except Exception as e:
        logger.warning(f"⚠️  Redis unavailable (cache disabled): {e!r}")
        app.state.redis = None

    # Database (optional).
    try:
        from db import create_db_pool
        app.state.db_pool = await asyncio.wait_for(
            create_db_pool(), timeout=config.STARTUP_TIMEOUT
        )
        logger.info("✅ Database pool ready")
    except Exception as e:
        logger.warning(f"⚠️  Database unavailable (health checks not persisted): {e!r}")
        app.state.db_pool = None

    # Regime scheduler (Part 3.4 decision 8). The cooldown clock and the
    # status dict exist either way (small, and /health reads the status);
    # the task only when SCHEDULER_ENABLED is true. It starts even with both
    # dependencies down: checks then run without cache, publish or rows.
    from cache import MemoryCooldowns
    app.state.cooldowns = MemoryCooldowns()
    app.state.check_status = {"lastCheckAt": None, "lastKind": None, "lastScore": None, "lastError": None}
    app.state.scheduler_task = None
    if settings.scheduler_enabled:
        import scheduler
        app.state.scheduler_task = asyncio.create_task(scheduler.run_scheduler(app.state))
        logger.info("✅ Regime scheduler started")
    else:
        logger.info("Regime scheduler disabled (SCHEDULER_ENABLED is not true)")

    logger.info(f"Risk Shield ready on port {settings.service_port}")
    yield

    # Shutdown
    logger.info("Shutting down Risk Shield...")
    # The scheduler stops before the pool and Redis close, so a check never
    # runs on a closed connection. asyncio.wait, not wait_for: wait_for would
    # block on a task that does not honour the cancel.
    task = getattr(app.state, "scheduler_task", None)
    if task is not None:
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=config.SCHEDULER_SHUTDOWN_TIMEOUT)
        if not done:
            logger.warning(
                f"Regime scheduler did not stop within {config.SCHEDULER_SHUTDOWN_TIMEOUT}s; "
                "closing connections anyway"
            )
        elif not task.cancelled() and task.exception() is not None:
            logger.warning(f"Regime scheduler ended with {task.exception()!r}")
        else:
            logger.info("Regime scheduler stopped")
    if getattr(app.state, "db_pool", None) is not None:
        await app.state.db_pool.close()
        logger.info("Database pool closed")
    if getattr(app.state, "redis", None) is not None:
        await app.state.redis.close()
        logger.info("Redis connection closed")


# ── App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="TradingFirm — Risk Shield",
    description="Market health monitoring, regime detection, and crash guard",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """
    Health check endpoint.

    `db_connected` / `redis_connected` report the outcome of the startup
    attempt, not a live probe — the same contract data-engine's /health
    has. A dependency that dies after boot still reads true until the
    service restarts (Part 3.1 decision 7, deferred).
    """
    return {
        "service": settings.service_name,
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "0.2.0",
        "db_connected": getattr(app.state, "db_pool", None) is not None,
        "redis_connected": getattr(app.state, "redis", None) is not None,
        "fredConfigured": settings.fred_configured,
        # Part 3.4: whether this process schedules checks, and its last one.
        "schedulerEnabled": settings.scheduler_enabled,
        "lastCheckAt": (getattr(app.state, "check_status", None) or {}).get("lastCheckAt"),
    }


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": settings.service_name,
        "description": "Market health monitoring, regime detection, and crash guard",
        "docs": "/docs",
        "endpoints": [
            "GET  /health",
            "GET  /market/health",
            "GET  /market/indicators",
            "GET  /market/history?days=30",
        ],
    }


# ── /market (Part 3.4, spec decision 7) ──────────────────────────
# Postgres only: an endpoint never computes a score or downloads, so the
# current answer and the history cannot disagree, and a failed insert shows
# up as an older checkedAt.

REGIME_MESSAGES = {
    "HEALTHY": "Market conditions are favorable",
    "CAUTIOUS": "Elevated risk — trade with caution",
    "DANGER": "High risk — consider reducing exposure",
    "CRITICAL": "⚠️ PROTECT CAPITAL — market in distress",
}
NO_CHECKS_DETAIL = "no health checks yet"      # never FastAPI's "Not Found" of a wrong route
DB_UNAVAILABLE_DETAIL = "database unavailable"
HISTORY_DAYS_DEFAULT = 30
HISTORY_DAYS_MAX = 90


async def _read(helper, *args):
    """One db read helper; no pool or a database failure is a 503."""
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE_DETAIL)
    try:
        return await helper(pool, *args)
    except db.DB_FAILURES as e:
        logger.warning(f"/market read failed: {e!r}")
        raise HTTPException(status_code=503, detail=DB_UNAVAILABLE_DETAIL) from None


async def _latest_row() -> dict:
    row = await _read(db.latest_health_check)
    if row is None:
        raise HTTPException(status_code=404, detail=NO_CHECKS_DETAIL)
    return row


def _indicators(row: dict) -> dict:
    """The row's indicators JSONB as a dict, {} when it is not a JSON object
    (asyncpg returns jsonb as text when no codec is set)."""
    value = row.get("indicators")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _valid_monitors(value: Any) -> Optional[dict]:
    if isinstance(value, dict) and all(isinstance(m, dict) for m in value.values()):
        return value
    return None


@app.get("/market/health")
async def market_health():
    """
    The latest check. settleScore / settleCheckedAt are the trend base (the
    latest scored settle before that check's session open), not the last
    published score, which lives only in the tf:risk:health payload.
    lastScored appears only when the latest check has no score.
    """
    row = await _latest_row()
    ind = _indicators(row)
    checked_at = row["checked_at"]
    body = {
        "score": row["score"],
        "regime": row["regime"],
        "trend": row["trend"],
        "settleScore": ind.get("settleScore"),
        "settleCheckedAt": ind.get("settleCheckedAt"),
        "message": REGIME_MESSAGES.get(row["regime"]),
        "checkedAt": checked_at.isoformat(),
        "ageSeconds": int((datetime.now(timezone.utc) - checked_at).total_seconds()),
        "kind": ind.get("kind"),
        "coverage": ind.get("coverage"),
        "stale": ind.get("stale"),
    }
    if row["score"] is None:
        scored = await _read(db.latest_scored_health_check)
        body["lastScored"] = (
            {"score": scored["score"], "regime": scored["regime"],
             "checkedAt": scored["checked_at"].isoformat()}
            if scored is not None else None
        )
    return body


@app.get("/market/indicators")
async def market_indicators():
    """The six monitors of the latest check, each 3.3's contract + weight.
    A row whose JSONB has the wrong shape answers monitors: null, never 500."""
    row = await _latest_row()
    ind = _indicators(row)
    return {
        "checkedAt": row["checked_at"].isoformat(),
        "kind": ind.get("kind"),
        "coverage": ind.get("coverage"),
        "inputs": ind.get("inputs"),
        "monitors": _valid_monitors(ind.get("monitors")),
    }


@app.get("/market/history")
async def market_history(days: int = Query(HISTORY_DAYS_DEFAULT, ge=1, le=HISTORY_DAYS_MAX)):
    """Checks over the last `days`, ascending, null scores included. An
    empty window is 200 with rows: [] (Part 1.4's rule), not a 404."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await _read(db.health_history, since)
    return {
        "days": days,
        "rows": [
            {"checkedAt": r["checked_at"].isoformat(), "score": r["score"], "regime": r["regime"],
             "trend": r["trend"], "kind": r["kind"], "stale": r["stale"]}
            for r in rows
        ],
    }
