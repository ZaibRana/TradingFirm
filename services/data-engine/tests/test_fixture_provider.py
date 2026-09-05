"""
TradingFirm — Fixture Provider Tests

Verifies FixtureProvider round-trips OHLCV + info data written in the
same format tests/record_fixture_live.py produces. Builds its own
temp fixtures dir — zero dependency on real recorded data, zero
network calls.
"""

import json

import numpy as np
import pandas as pd
import pytest

from providers.fixture_provider import DEFAULT_INFO, FixtureProvider


def make_ohlcv_df(rows: int = 5, freq: str = "B") -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=rows, freq=freq)
    dates.name = "Date"
    closes = 100 + np.arange(rows, dtype=float)
    return pd.DataFrame(
        {
            "Open": closes - 0.5,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": np.full(rows, 1_000_000, dtype=float),
        },
        index=dates,
    )


@pytest.fixture
def fixtures_dir(tmp_path):
    for sub in ("daily", "hourly", "info"):
        (tmp_path / sub).mkdir()

    daily_df = make_ohlcv_df(rows=5, freq="B")
    daily_df.to_json(tmp_path / "daily" / "AAPL.json", orient="table")

    hourly_df = make_ohlcv_df(rows=8, freq="h")
    hourly_df.to_json(tmp_path / "hourly" / "AAPL.json", orient="table")

    info = {
        "name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "marketCap": 3_000_000_000_000,
        "floatShares": 15_000_000_000,
        "floatStr": "15.0B",
        "news": [{"title": "Test headline", "url": "http://x", "publisher": "Reuters"}],
    }
    (tmp_path / "info" / "AAPL.json").write_text(json.dumps(info))

    return tmp_path, daily_df, hourly_df, info


class TestFixtureProvider:
    def test_provider_name(self, fixtures_dir):
        tmp_path, *_ = fixtures_dir
        provider = FixtureProvider(fixtures_dir=tmp_path)
        assert provider.provider_name == "fixture"

    @pytest.mark.asyncio
    async def test_get_candidates_returns_recorded_tickers(self, fixtures_dir):
        tmp_path, *_ = fixtures_dir
        provider = FixtureProvider(fixtures_dir=tmp_path)

        tickers, status = await provider.get_candidates()

        assert tickers == ["AAPL"]
        assert status == "market_open"

    @pytest.mark.asyncio
    async def test_download_daily_round_trips_ohlcv(self, fixtures_dir):
        tmp_path, daily_df, _, _ = fixtures_dir
        provider = FixtureProvider(fixtures_dir=tmp_path)

        bulk = await provider.download_daily(["AAPL"])
        df = provider.extract_ticker_df(bulk, "AAPL")

        assert df is not None
        # check_freq=False: JSON round-tripping drops DatetimeIndex.freq,
        # a pure convenience attribute real yfinance data doesn't carry either.
        pd.testing.assert_frame_equal(df, daily_df, check_freq=False)

    @pytest.mark.asyncio
    async def test_download_hourly_round_trips_ohlcv(self, fixtures_dir):
        tmp_path, _, hourly_df, _ = fixtures_dir
        provider = FixtureProvider(fixtures_dir=tmp_path)

        bulk = await provider.download_hourly(["AAPL"])
        df = provider.extract_ticker_df(bulk, "AAPL")

        assert df is not None
        pd.testing.assert_frame_equal(df, hourly_df, check_freq=False)

    @pytest.mark.asyncio
    async def test_extract_ticker_df_missing_ticker_returns_none(self, fixtures_dir):
        tmp_path, *_ = fixtures_dir
        provider = FixtureProvider(fixtures_dir=tmp_path)

        bulk = await provider.download_daily(["AAPL", "MISSING"])

        assert provider.extract_ticker_df(bulk, "MISSING") is None

    @pytest.mark.asyncio
    async def test_get_stock_info_returns_recorded_data(self, fixtures_dir):
        tmp_path, _, _, info = fixtures_dir
        provider = FixtureProvider(fixtures_dir=tmp_path)

        result = await provider.get_stock_info("AAPL")

        assert result == info

    @pytest.mark.asyncio
    async def test_get_stock_info_missing_ticker_returns_default(self, fixtures_dir):
        tmp_path, *_ = fixtures_dir
        provider = FixtureProvider(fixtures_dir=tmp_path)

        result = await provider.get_stock_info("NOPE")

        assert result == DEFAULT_INFO
