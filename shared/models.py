"""
TradingFirm — Shared Pydantic Models

Shared data models used across services for consistent
request/response schemas and type safety.

These models define the contract between services.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Stock Models ──

class StockBase(BaseModel):
    """Base stock information."""
    ticker: str = Field(..., pattern=r"^[A-Z]{1,5}$", description="Stock symbol")
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None


class StockDetail(StockBase):
    """Enriched stock with market data."""
    market_cap: Optional[int] = None
    float_shares: Optional[int] = None
    updated_at: Optional[datetime] = None


# ── Health Check Models ──

class ServiceHealth(BaseModel):
    """Standard health check response for all services."""
    service: str
    status: str = "ok"
    timestamp: datetime
    version: str = "0.1.0"
