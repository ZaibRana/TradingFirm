"""
TradingFirm — Scanner Pipeline Tests

Tests for the restructured daily-first pipeline:
  - Daily filter pass/reject cases
  - Hourly filter pass/reject cases
  - Pipeline integration: download_hourly called with only daily winners

All tests use synthetic OHLCV data and a mocked provider.
Zero live API calls.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ── Synthetic Data Generators ────────────────────────────────────


def make_daily_df(
    rows: int = 150,
    base_price: float = 25.0,
    daily_range: float = 1.0,
    volume_avg: float = 1_500_000,
    volume_last: float = 2_000_000,
    trend: float = 0.01,
) -> pd.DataFrame:
    """
    Generate synthetic daily OHLCV data.

    Args:
        rows: Number of trading days
        base_price: Starting price
        daily_range: Avg High-Low range (controls ATRP)
        volume_avg: Average volume for days 1..N-1
        volume_last: Volume for the last day (controls RVOL)
        trend: Per-bar price increment (controls 52w position)
    """
    dates = pd.bdate_range(end=datetime.now(), periods=rows)
    # Create a price path that peaks mid-series and pulls back,
    # so the 52-week position ends around 50% (not at extreme)
    np.random.seed(42)
    peak_idx = rows * 2 // 3  # peak at 2/3 through the series
    up = np.linspace(0, trend * rows, peak_idx)
    down = np.linspace(trend * rows, trend * rows * 0.5, rows - peak_idx)
    closes = base_price + np.concatenate([up, down])
    closes = closes + np.random.normal(0, 0.05, rows)

    highs = closes + daily_range / 2
    lows = closes - daily_range / 2

    volumes = np.full(rows, volume_avg, dtype=float)
    volumes[-1] = volume_last

    return pd.DataFrame(
        {"Open": closes - 0.1, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )


def make_hourly_df(
    rows: int = 300,
    base_price: float = 25.0,
    trend: float = 0.005,
) -> pd.DataFrame:
    """
    Generate synthetic hourly OHLCV data.

    For the EMA filters to pass, we need a consistent uptrend.
    For them to fail, use a downtrend (negative trend).
    """
    dates = pd.date_range(end=datetime.now(), periods=rows, freq="h")
    closes = base_price + np.arange(rows) * trend
    np.random.seed(42)
    closes = closes + np.random.normal(0, 0.02, rows)

    highs = closes + 0.15
    lows = closes - 0.15

    volumes = np.full(rows, 50_000, dtype=float)

    return pd.DataFrame(
        {"Open": closes - 0.05, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )


# ── Test: Daily Filters ─────────────────────────────────────────


class TestDailyFilters:
    """Tests for _apply_daily_filters() in isolation."""

    def _make_scanner(self):
        """Create a MarketScanner with a dummy provider."""
        from scanners.market_scanner import MarketScanner

        mock_provider = MagicMock()
        return MarketScanner(mock_provider)

    def test_daily_filters_pass(self):
        """Ticker with valid ATRP, RVOL, 52w position passes daily filters."""
        scanner = self._make_scanner()

        # daily_range=1.0 on $25 stock -> ATRP ~ 4% (within 2.5-6%)
        # volume_last=2M vs avg=1.5M -> RVOL ~ 1.33 (> 1.0 weekend)
        # trend=0.01 over 150 days -> price moves ~$1.50, 52w pos ~ 50%
        daily_df = make_daily_df(
            rows=150,
            base_price=25.0,
            daily_range=1.0,
            volume_avg=1_500_000,
            volume_last=2_000_000,
            trend=0.01,
        )

        passed, result = scanner._apply_daily_filters("TEST", daily_df, "weekend")

        assert passed is True, f"Expected pass but got rejection: {result}"
        assert isinstance(result, dict)
        assert "atrp" in result
        assert "rvol" in result
        assert "pos52w" in result
        assert "price" in result
        assert 2.5 <= result["atrp"] <= 6.0, f"ATRP {result['atrp']} out of range"
        assert result["rvol"] > 1.0, f"RVOL {result['rvol']} should be > 1.0"
        print(f"  PASS: ATRP={result['atrp']}%, RVOL={result['rvol']}x, 52w={result['pos52w']}%")

    def test_daily_filters_reject_atrp_too_low(self):
        """Ticker with ATRP < 2.5% gets rejected."""
        scanner = self._make_scanner()

        # daily_range=0.2 on $25 stock -> ATRP ~ 0.8% (< 2.5%)
        daily_df = make_daily_df(
            rows=150,
            base_price=25.0,
            daily_range=0.2,
            volume_avg=1_500_000,
            volume_last=2_000_000,
        )

        passed, result = scanner._apply_daily_filters("SLOW", daily_df, "weekend")

        assert passed is False, f"Expected rejection but passed with: {result}"
        assert "ATRP" in result, f"Rejection reason should mention ATRP: {result}"
        print(f"  REJECTED: {result}")

    def test_daily_filters_reject_rvol_too_low(self):
        """Ticker with low RVOL gets rejected."""
        scanner = self._make_scanner()

        # volume_last=500K vs avg=1.5M -> RVOL ~ 0.33 (< 1.0)
        daily_df = make_daily_df(
            rows=150,
            base_price=25.0,
            daily_range=1.0,
            volume_avg=1_500_000,
            volume_last=500_000,
        )

        passed, result = scanner._apply_daily_filters("DEAD", daily_df, "weekend")

        assert passed is False, f"Expected rejection but passed with: {result}"
        assert "RVOL" in result, f"Rejection reason should mention RVOL: {result}"
        print(f"  REJECTED: {result}")

    def test_daily_filters_reject_too_new(self):
        """Ticker with < 120 trading days gets rejected (IPO check)."""
        scanner = self._make_scanner()

        daily_df = make_daily_df(rows=60, base_price=25.0, daily_range=1.0)

        passed, result = scanner._apply_daily_filters("IPO", daily_df, "weekend")

        assert passed is False, f"Expected rejection but passed with: {result}"
        assert "Too new" in result, f"Rejection reason should mention 'Too new': {result}"
        print(f"  REJECTED: {result}")


# ── Test: Hourly Filters ────────────────────────────────────────


class TestHourlyFilters:
    """Tests for _apply_hourly_filters() in isolation."""

    def _make_scanner(self):
        from scanners.market_scanner import MarketScanner

        mock_provider = MagicMock()
        return MarketScanner(mock_provider)

    def test_hourly_filters_pass(self):
        """Ticker with uptrending hourly data passes 4H/1H EMA filters."""
        scanner = self._make_scanner()

        # Uptrend -> EMA20 > EMA50, price > 50 EMA
        hourly_df = make_hourly_df(rows=300, base_price=25.0, trend=0.005)
        daily_data = {"atrp": 4.0, "rvol": 1.5, "price": 25.0}

        passed, result = scanner._apply_hourly_filters("UP", hourly_df, daily_data)

        assert passed is True, f"Expected pass but got rejection: {result}"
        assert isinstance(result, dict)
        # Should return the daily_data passed through
        assert result["atrp"] == 4.0
        assert result["rvol"] == 1.5
        print(f"  PASS: Hourly filters passed, data preserved")

    def test_hourly_filters_reject_downtrend(self):
        """Ticker with downtrending hourly data fails EMA filters."""
        scanner = self._make_scanner()

        # Strong downtrend -> EMA20 < EMA50
        hourly_df = make_hourly_df(rows=300, base_price=30.0, trend=-0.01)
        daily_data = {"atrp": 4.0, "rvol": 1.5, "price": 25.0}

        passed, result = scanner._apply_hourly_filters("DOWN", hourly_df, daily_data)

        assert passed is False, f"Expected rejection but passed with: {result}"
        assert isinstance(result, str)
        print(f"  REJECTED: {result}")


# ── Test: Pipeline Integration ──────────────────────────────────


class TestPipelineIntegration:
    """Test that download_hourly is called with only daily winners."""

    @pytest.mark.asyncio
    async def test_pipeline_downloads_hourly_for_daily_winners_only(self):
        """
        Core pipeline test: verify that download_hourly() receives only
        the tickers that passed daily filters, NOT all candidates.
        """
        from scanners.market_scanner import MarketScanner

        # Set up 3 tickers -- only GOOD will pass daily filters
        all_tickers = ["GOOD", "BAD_ATRP", "BAD_RVOL"]

        # Build daily data: GOOD passes, others fail
        good_daily = make_daily_df(
            rows=150, base_price=25.0, daily_range=1.0,
            volume_avg=1_500_000, volume_last=2_000_000, trend=0.01,
        )
        bad_atrp_daily = make_daily_df(
            rows=150, base_price=25.0, daily_range=0.2,  # ATRP too low
            volume_avg=1_500_000, volume_last=2_000_000,
        )
        bad_rvol_daily = make_daily_df(
            rows=150, base_price=25.0, daily_range=1.0,
            volume_avg=1_500_000, volume_last=500_000,  # RVOL too low
        )

        # Build hourly data for GOOD (uptrend -> passes hourly filters)
        good_hourly = make_hourly_df(rows=300, base_price=25.0, trend=0.005)

        # Mock provider
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"

        # get_candidates returns all 3 tickers
        mock_provider.get_candidates = AsyncMock(
            return_value=(all_tickers, "weekend")
        )

        # download_daily returns a fake bulk object
        # Must set empty=False so canary check doesn't abort
        daily_bulk = MagicMock()
        daily_bulk.empty = False
        mock_provider.download_daily = AsyncMock(return_value=daily_bulk)

        # download_hourly returns a different fake bulk object
        hourly_bulk = MagicMock()
        hourly_bulk.empty = False
        mock_provider.download_hourly = AsyncMock(return_value=hourly_bulk)

        # extract_ticker_df returns the appropriate data per ticker and bulk
        def extract_fn(bulk, ticker):
            if bulk is daily_bulk:
                return {
                    "GOOD": good_daily,
                    "BAD_ATRP": bad_atrp_daily,
                    "BAD_RVOL": bad_rvol_daily,
                }.get(ticker)
            if bulk is hourly_bulk:
                return {"GOOD": good_hourly}.get(ticker)
            return None

        mock_provider.extract_ticker_df = MagicMock(side_effect=extract_fn)

        # get_stock_info for enrichment (GOOD passes gates)
        mock_provider.get_stock_info = AsyncMock(return_value={
            "name": "Good Corp",
            "sector": "Technology",
            "industry": "Software",
            "marketCap": 5_000_000_000,
            "floatShares": 100_000_000,
            "floatStr": "100M",
            "news": [],
        })

        # Run the scanner
        scanner = MarketScanner(mock_provider)
        result = await scanner.run_scan(price_min=10.0, price_max=40.0)

        # ── KEY ASSERTION: download_hourly was called with ONLY ["GOOD"] ──
        mock_provider.download_hourly.assert_called_once()
        hourly_call_tickers = mock_provider.download_hourly.call_args[0][0]
        assert "GOOD" in hourly_call_tickers, (
            f"GOOD should be in hourly download list: {hourly_call_tickers}"
        )
        assert "BAD_ATRP" not in hourly_call_tickers, (
            f"BAD_ATRP should NOT be in hourly download list: {hourly_call_tickers}"
        )
        assert "BAD_RVOL" not in hourly_call_tickers, (
            f"BAD_RVOL should NOT be in hourly download list: {hourly_call_tickers}"
        )
        assert len(hourly_call_tickers) == 1, (
            f"Expected 1 ticker for hourly download, got {len(hourly_call_tickers)}: "
            f"{hourly_call_tickers}"
        )

        print(f"  download_hourly called with {hourly_call_tickers} (not all {len(all_tickers)})")
        print(f"  Scan result: {result.passed_count} stocks passed")
