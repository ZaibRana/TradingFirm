"""
TradingFirm — Risk Shield (Service 3)

Responsibilities:
  - Market health scoring (VIX, breadth, sector rotation, etc.)
  - Regime detection (HEALTHY / CAUTIOUS / DANGER / CRITICAL)
  - Crash guard alerts
  - Health check scheduling (every 5 min during market hours)

Port: 8003
"""

import os
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = os.getenv("SERVICE_NAME", "risk-shield")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8003"))

app = FastAPI(
    title="TradingFirm — Risk Shield",
    description="Market health monitoring, regime detection, and crash guard",
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
        "description": "Market health monitoring, regime detection, and crash guard",
        "docs": "/docs",
        "health": "/health",
    }
