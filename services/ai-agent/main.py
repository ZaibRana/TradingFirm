"""
TradingFirm — AI Agent (Service 5)

Responsibilities:
  - Signal grading (0–100 confidence score via LLM)
  - Multi-provider support (Gemini Flash primary, Claude fallback)
  - Pattern learning from historical signal outcomes
  - Weekly accuracy self-assessment
  - Natural language query interface (future)

Port: 8004
"""

import os
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = os.getenv("SERVICE_NAME", "ai-agent")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8004"))

app = FastAPI(
    title="TradingFirm — AI Agent",
    description="LLM-powered signal grading, pattern learning, and reporting",
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
        "description": "LLM-powered signal grading, pattern learning, and reporting",
        "docs": "/docs",
        "health": "/health",
    }
