"""
TradingFirm — Fixture Data Provider

Offline DataProvider implementation backed by recorded JSON fixtures
in tests/fixtures/{daily,hourly,info}/<TICKER>.json. No network calls —
used by unit tests so they never hit yfinance or Finviz (AGENTS.md G7).

Fixtures are recorded once via tests/record_fixture_live.py, a manual
live script — never run automatically or in CI.
"""

import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from providers.base import DataProvider

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

DEFAULT_INFO = {
    "name": "",
    "sector": "Other",
    "industry": "Other",
    "marketCap": 0,
    "floatShares": 0,
    "floatStr": "N/A",
    "news": [],
}


class FixtureProvider(DataProvider):
    """Reads recorded OHLCV + info fixtures from disk. No network I/O."""

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self.fixtures_dir = Path(fixtures_dir)

    @property
    def provider_name(self) -> str:
        return "fixture"

    def _tickers(self) -> list[str]:
        daily_dir = self.fixtures_dir / "daily"
        if not daily_dir.exists():
            return []
        return sorted(p.stem for p in daily_dir.glob("*.json"))

    async def get_candidates(
        self,
        price_min: float = 10.0,
        price_max: float = 40.0,
    ) -> tuple[list[str], str]:
        """Returns every ticker that has a recorded daily fixture."""
        return self._tickers(), "market_open"

    async def download_daily(self, tickers: list[str], period: str = "1y") -> Any:
        return self._load_bulk("daily", tickers)

    async def download_hourly(self, tickers: list[str], period: str = "3mo") -> Any:
        return self._load_bulk("hourly", tickers)

    def _load_bulk(self, interval_dir: str, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Bulk format for this provider: {ticker: DataFrame}."""
        bulk = {}
        for ticker in tickers:
            path = self.fixtures_dir / interval_dir / f"{ticker}.json"
            if path.exists():
                bulk[ticker] = pd.read_json(path, orient="table")
        return bulk

    def extract_ticker_df(
        self,
        bulk_data: Any,
        ticker: str,
    ) -> Optional[pd.DataFrame]:
        if not bulk_data:
            return None
        return bulk_data.get(ticker)

    async def get_stock_info(self, ticker: str) -> dict:
        path = self.fixtures_dir / "info" / f"{ticker}.json"
        if not path.exists():
            return dict(DEFAULT_INFO)
        return json.loads(path.read_text())
