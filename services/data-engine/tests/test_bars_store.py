"""
TradingFirm — OHLCV Bar Store Tests

Verifies upsert_bars() and get_bars() build the correct SQL and
parameters against a fully mocked asyncpg pool. Zero network, zero
real database.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from db import get_bars, upsert_bars


def _make_pool(fetch_return=None):
    """Build a MagicMock asyncpg pool whose acquire() context manager
    yields a connection with async executemany/fetch."""
    conn = AsyncMock()
    conn.executemany = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])

    pool = MagicMock()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


@pytest.mark.asyncio
async def test_upsert_bars_empty_list_is_noop():
    pool, conn = _make_pool()

    result = await upsert_bars(pool, "AAPL", "1d", [])

    assert result == 0
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_bars_sends_correct_sql_and_params():
    pool, conn = _make_pool()
    ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
    bars = [
        {"ts": ts, "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1_000_000},
    ]

    result = await upsert_bars(pool, "AAPL", "1d", bars)

    assert result == 1
    conn.executemany.assert_awaited_once()
    query, records = conn.executemany.await_args.args
    assert "INSERT INTO data_engine.ohlcv_bars" in query
    assert "ON CONFLICT (ticker, interval, ts) DO UPDATE" in query
    assert records == [("AAPL", "1d", ts, 10.0, 11.0, 9.5, 10.5, 1_000_000)]


@pytest.mark.asyncio
async def test_get_bars_without_since_omits_ts_filter():
    pool, conn = _make_pool(fetch_return=[])

    result = await get_bars(pool, "AAPL", "1d")

    assert result == []
    conn.fetch.assert_awaited_once()
    query, *args = conn.fetch.await_args.args
    assert "WHERE ticker = $1 AND interval = $2" in query
    assert "ts >=" not in query
    assert "ORDER BY ts ASC" in query
    assert args == ["AAPL", "1d"]


@pytest.mark.asyncio
async def test_get_bars_with_since_filters_and_maps_rows():
    ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
    since = datetime(2026, 8, 1, tzinfo=timezone.utc)
    fake_row = {
        "ts": ts,
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 1_000_000,
    }
    pool, conn = _make_pool(fetch_return=[fake_row])

    result = await get_bars(pool, "AAPL", "1d", since=since)

    conn.fetch.assert_awaited_once()
    query, *args = conn.fetch.await_args.args
    assert "ts >= $3" in query
    assert args == ["AAPL", "1d", since]
    assert result == [fake_row]
