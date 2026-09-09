"""
TradingFirm — Risk Shield (Service 3)

Responsibilities:
  - Market health scoring (VIX, breadth, sector rotation, etc.)
  - Regime detection (HEALTHY / CAUTIOUS / DANGER / CRITICAL)
  - Crash guard alerts
  - Health check scheduling (every 5 min during market hours)

Endpoints today (Part 3.1 is the skeleton; the market endpoints land in 3.4):
  GET /health — health check, including dependency state at boot
  GET /       — service info

Port: 8003
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
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

    logger.info(f"Risk Shield ready on port {settings.service_port}")
    yield

    # Shutdown
    logger.info("Shutting down Risk Shield...")
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
        ],
    }
