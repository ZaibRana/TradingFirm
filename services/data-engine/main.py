"""
TradingFirm — Data Engine (Service 1)

FastAPI application with endpoints:
  POST /scan/run       — trigger a new market scan (background)
  GET  /scan/results   — latest scan results (cached or DB)
  GET  /scan/history   — past scan metadata
  GET  /scan/status    — current scan status (idle/running/completed)
  GET  /stocks/{ticker} — enriched data for one stock
  GET  /market/status  — current market session
  GET  /health         — health check
  GET  /               — service info

Port: 8001
"""

import logging
import time as _time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from cache import (
    cache_scan_result,
    create_redis,
    get_cached_scan,
    get_scan_status,
    publish_scan_complete,
    set_scan_status,
)
from config import settings
from db import create_db_pool, get_latest_scan, get_scan_history, save_scan_result, upsert_stocks
from providers import get_provider
from scanners.market_scanner import MarketScanner
from scanners.market_status import get_market_status
from scanners.models import ScanRequest, ScanResult

# ── Logging ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("data-engine")


# ── Lifespan (startup/shutdown) ──────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB pool and Redis on startup, close on shutdown."""
    logger.info("Starting Data Engine...")

    # Startup
    try:
        app.state.db_pool = await create_db_pool()
        logger.info("✅ Database pool ready")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        app.state.db_pool = None

    try:
        app.state.redis = await create_redis()
        logger.info("✅ Redis connection ready")
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        app.state.redis = None

    app.state.provider = get_provider(settings.data_provider)
    logger.info(f"✅ Data provider: {app.state.provider.provider_name}")

    app.state.scanner = MarketScanner(app.state.provider)
    logger.info("✅ Scanner initialized")

    logger.info(f"Data Engine ready on port {settings.service_port}")
    yield

    # Shutdown
    logger.info("Shutting down Data Engine...")
    if app.state.db_pool:
        await app.state.db_pool.close()
        logger.info("Database pool closed")
    if app.state.redis:
        await app.state.redis.close()
        logger.info("Redis connection closed")
    # Close the requests.Session used by yfinance provider
    if hasattr(app.state, 'provider') and hasattr(app.state.provider, '_session'):
        app.state.provider._session.close()
        logger.info("Provider HTTP session closed")


# ── App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="TradingFirm — Data Engine",
    description="Market data scanning, indicators, and fundamentals",
    version="0.1.0",
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


# ── Background Scan Task ─────────────────────────────────────────

async def _run_scan_background(
    app_state,
    price_min: float,
    price_max: float,
    advanced: bool,
):
    """Background task: run full scan, save to DB, cache, publish event."""
    try:
        # Mark scan as running
        if app_state.redis:
            await set_scan_status(app_state.redis, "running", "Scan in progress...")

        # Run the scanner
        result: ScanResult = await app_state.scanner.run_scan(
            price_min=price_min,
            price_max=price_max,
            advanced=advanced,
        )

        result_dict = result.model_dump(mode="json", by_alias=True)

        # Save to database
        if app_state.db_pool:
            try:
                await save_scan_result(
                    pool=app_state.db_pool,
                    scanned_at=result.timestamp,
                    market_status=result.market_status,
                    total_screened=result.total_scanned,
                    total_passed=result.passed_count,
                    duration_seconds=result.duration_seconds,
                    stocks=result_dict["stocks"],
                )
                # Upsert stock records
                await upsert_stocks(app_state.db_pool, result_dict["stocks"])
            except Exception as e:
                logger.error(f"DB save failed: {e}")

        # Cache in Redis
        if app_state.redis:
            try:
                await cache_scan_result(app_state.redis, result_dict)
                await publish_scan_complete(app_state.redis, result_dict)
                await set_scan_status(
                    app_state.redis,
                    "completed",
                    f"Scan complete: {result.passed_count} stocks found",
                )
            except Exception as e:
                logger.error(f"Redis cache/publish failed: {e}")

        logger.info(f"Background scan complete: {result.passed_count} stocks")

    except Exception as e:
        logger.error(f"Background scan failed: {e}", exc_info=True)
        if app_state.redis:
            try:
                await set_scan_status(app_state.redis, "failed", str(e))
            except Exception:
                pass


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "service": settings.service_name,
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
        "provider": settings.data_provider,
        "db_connected": app.state.db_pool is not None,
        "redis_connected": app.state.redis is not None,
    }


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": settings.service_name,
        "description": "Market data scanning, indicators, and fundamentals",
        "docs": "/docs",
        "endpoints": [
            "POST /scan/run",
            "GET  /scan/results",
            "GET  /scan/history",
            "GET  /scan/status",
            "GET  /stocks/{ticker}",
            "GET  /market/status",
            "GET  /health",
        ],
    }


@app.post("/scan/run", status_code=202)
async def scan_run(
    request: ScanRequest,
    background_tasks: BackgroundTasks,
):
    """
    Trigger a new market scan in the background.

    A full scan takes 1–5 minutes. This endpoint returns immediately
    with status 202. Poll GET /scan/status to check progress,
    then GET /scan/results for data.
    """
    # Check if scan is already running
    if app.state.redis:
        status = await get_scan_status(app.state.redis)
        if status.get("status") == "running":
            return {
                "status": "already_running",
                "message": "A scan is already in progress. Check GET /scan/status.",
            }

        # ⚠️ RATE LIMIT: 10-minute cooldown between scans
        last_scan_time = await app.state.redis.get("tf:cache:last_scan_time")
        if last_scan_time:
            elapsed = _time.time() - float(last_scan_time)
            if elapsed < 600:  # 10 minutes
                remaining = int(600 - elapsed)
                return {
                    "status": "cooldown",
                    "message": f"Scan cooldown: {remaining}s remaining. Min 10 minutes between scans.",
                    "retry_after_seconds": remaining,
                }

        # Record scan start time
        await app.state.redis.set("tf:cache:last_scan_time", str(_time.time()), ex=3600)

    # Launch background scan
    background_tasks.add_task(
        _run_scan_background,
        app.state,
        request.price_min,
        request.price_max,
        request.advanced,
    )

    return {
        "status": "scan_started",
        "message": f"Scan started (${request.price_min}-${request.price_max}, advanced={request.advanced}). "
                   f"Poll GET /scan/status for progress.",
    }


@app.get("/scan/results")
async def scan_results(
    use_cache: bool = Query(True, description="Try Redis cache first"),
):
    """
    Get the latest scan results.

    Checks Redis cache first (fast), falls back to database.
    """
    # Try cache first
    if use_cache and app.state.redis:
        try:
            cached = await get_cached_scan(app.state.redis)
            if cached:
                cached["source"] = "cache"
                return cached
        except Exception as e:
            logger.warning(f"Cache read failed: {e}")

    # Fall back to database
    if app.state.db_pool:
        try:
            db_result = await get_latest_scan(app.state.db_pool)
            if db_result:
                db_result["source"] = "database"
                return db_result
        except Exception as e:
            logger.error(f"DB read failed: {e}")
            raise HTTPException(status_code=500, detail=f"Database error: {e}")

    # No results anywhere
    return {
        "source": "none",
        "message": "No scan results available. Run POST /scan/run first.",
        "stocks": [],
    }


@app.get("/scan/history")
async def scan_history(
    limit: int = Query(10, ge=1, le=100, description="Number of scans to return"),
):
    """List past scan runs (metadata only, no stocks array)."""
    if not app.state.db_pool:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        history = await get_scan_history(app.state.db_pool, limit=limit)
        return {"scans": history, "count": len(history)}
    except Exception as e:
        logger.error(f"History query failed: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")


@app.get("/scan/status")
async def scan_status():
    """Check if a scan is currently running."""
    if not app.state.redis:
        return {"status": "unknown", "message": "Redis not connected"}

    try:
        status = await get_scan_status(app.state.redis)
        return status
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/stocks/{ticker}")
async def get_stock(ticker: str):
    """Get enriched data for a specific stock ticker."""
    # Validate ticker format
    ticker = ticker.upper().strip()
    if not ticker.isalpha() or len(ticker) > 5:
        raise HTTPException(status_code=400, detail="Invalid ticker format")

    try:
        info = await app.state.provider.get_stock_info(ticker)
        if not info.get("name"):
            raise HTTPException(
                status_code=404,
                detail=f"Ticker '{ticker}' not found or returned no data",
            )
        return {
            "ticker": ticker,
            **info,
        }
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"Stock info failed for {ticker}: {e}")
        if "rate" in error_msg or "too many" in error_msg or "429" in error_msg:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limited by data provider. Try again in 30 seconds.",
            )
        raise HTTPException(
            status_code=502,
            detail=f"Data provider error for {ticker}: {e}",
        )


@app.get("/market/status")
async def market_status():
    """Get current US market session status."""
    status, et = get_market_status()
    return {
        "status": status,
        "timestamp": et.isoformat(),
        "display": et.strftime("%A %I:%M %p ET"),
    }
