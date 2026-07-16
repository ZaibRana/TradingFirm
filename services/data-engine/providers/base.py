"""
TradingFirm — Data Provider Interface (Abstract Base)

All data sources (yfinance, FMP, Polygon, etc.) implement this
interface. Swap providers by changing the DATA_PROVIDER env var.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

import pandas as pd


class DataProvider(ABC):
    """
    Abstract interface for market data providers.

    Implementing classes must wrap any blocking I/O in
    asyncio.to_thread() since most finance APIs are synchronous.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider name (e.g., 'yfinance')."""
        ...

    @abstractmethod
    async def get_candidates(
        self,
        price_min: float = 10.0,
        price_max: float = 40.0,
    ) -> tuple[list[str], str]:
        """
        Pre-screen stocks from a broad universe.

        Args:
            price_min: Minimum stock price filter
            price_max: Maximum stock price filter

        Returns:
            (tickers, market_status) where:
            - tickers: sorted list of candidate ticker symbols
            - market_status: current market session string
        """
        ...

    @abstractmethod
    async def download_daily(
        self,
        tickers: list[str],
        period: str = "1y",
    ) -> Any:
        """
        Bulk download daily OHLCV candles.

        Args:
            tickers: List of ticker symbols
            period: Lookback period (e.g., '1y', '6mo')

        Returns:
            Bulk data object (provider-specific format).
            Use extract_ticker_df() to get individual DataFrames.
        """
        ...

    @abstractmethod
    async def download_hourly(
        self,
        tickers: list[str],
        period: str = "3mo",
    ) -> Any:
        """
        Bulk download hourly (1H) OHLCV candles.

        Args:
            tickers: List of ticker symbols
            period: Lookback period (e.g., '3mo')

        Returns:
            Bulk data object (provider-specific format).
        """
        ...

    @abstractmethod
    def extract_ticker_df(
        self,
        bulk_data: Any,
        ticker: str,
    ) -> Optional[pd.DataFrame]:
        """
        Extract a single ticker's OHLCV DataFrame from bulk data.

        Args:
            bulk_data: Return value from download_daily/download_hourly
            ticker: Symbol to extract

        Returns:
            DataFrame with Open, High, Low, Close, Volume columns,
            or None if ticker data is missing/empty.
        """
        ...

    @abstractmethod
    async def get_stock_info(self, ticker: str) -> dict:
        """
        Fetch enrichment data for a single stock.

        Returns dict with keys:
            name, sector, industry, marketCap, floatShares,
            floatStr, news (list of {title, url, publisher})
        """
        ...
