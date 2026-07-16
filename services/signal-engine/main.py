"""
TradingFirm — Signal Engine (Service 2)

Responsibilities:
  - Support/Resistance zone detection (fractal, volume cluster, pivot)
  - Signal generation (entry/exit logic, stop loss, take profit)
  - Signal lifecycle tracking (PENDING → TRIGGERED → outcome)
  - Confidence scoring

Port: 8002
"""

import os
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = os.getenv("SERVICE_NAME", "signal-engine")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8002"))

app = FastAPI(
    title="TradingFirm — Signal Engine",
    description="S/R zone detection, signal generation, and lifecycle tracking",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
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
    """Health check endpoint."""
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
    }


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": SERVICE_NAME,
        "description": "S/R zone detection, signal generation, and lifecycle tracking",
        "docs": "/docs",
        "health": "/health",
    }
