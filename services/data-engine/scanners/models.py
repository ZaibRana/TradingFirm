"""
TradingFirm — Scanner Pydantic Models

Defines the data contract for scan results.
Used by API endpoints, database layer, and cache.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """A news headline associated with a stock."""
    title: str = ""
    url: str = ""
    publisher: str = ""


class StockResult(BaseModel):
    """A stock that passed all scanner filters."""
    symbol: str = Field(..., description="Ticker symbol (e.g. MSFT)")
    name: str = ""
    price: float = 0.0
    sector: str = "Other"
    industry: str = "Other"
    market_cap: int = Field(0, alias="marketCap")
    float_shares: int = Field(0, alias="floatShares")
    float_str: str = Field("N/A", alias="floatStr")
    fifty_two_week_high: float = Field(0.0, alias="fiftyTwoWeekHigh")
    fifty_two_week_low: float = Field(0.0, alias="fiftyTwoWeekLow")
    atrp: float = 0.0
    atr: float = 0.0
    rvol: float = 0.0
    pos_52w: int = Field(0, alias="pos52w")
    news: list[NewsItem] = []

    model_config = {"populate_by_name": True}


class ScanResult(BaseModel):
    """Complete scan run output."""
    scanner: str = "professional"
    timestamp: datetime
    duration_seconds: float = Field(0.0, alias="durationSeconds")
    market_status: str = Field("", alias="marketStatus")
    total_scanned: int = Field(0, alias="totalScanned")
    passed_count: int = Field(0, alias="passedCount")
    stocks: list[StockResult] = []

    model_config = {"populate_by_name": True}


class ScanRequest(BaseModel):
    """Request body for POST /scan/run."""
    price_min: float = Field(10.0, ge=1.0, le=500.0, description="Minimum stock price")
    price_max: float = Field(40.0, ge=1.0, le=1000.0, description="Maximum stock price")
    advanced: bool = Field(False, description="Enable 5M tradability filters")


class ScanStatusResponse(BaseModel):
    """Response for scan status queries."""
    status: str = "idle"
    message: str = ""
