"""
TradingFirm — Bars Endpoint Tests

Verifies GET /stock/{ticker}/bars against a fully mocked asyncpg pool.
DB only, zero network, zero real database — matches the style of
test_bars_store.py / test_refresh_endpoint.py.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import main
from providers.fixture_provider import FixtureProvider


def _row(ts, open_=10.0, high=11.0, low=9.5, close=10.5, volume=1_000_000):
    return {"ts": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _make_pool(fetch_return=None, fetch_side_effect=None):
    """Build a MagicMock asyncpg pool whose acquire() context manager
    yields a connection with async fetch (matches test_bars_store.py)."""
    conn = AsyncMock()
    if fetch_side_effect is not None:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=fetch_return or [])

    pool = MagicMock()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


@pytest.fixture(autouse=True)
def reset_app_state():
    """Give every test a clean, isolated app.state (module-level app is
    shared across the whole test session)."""
    main.app.state.provider = FixtureProvider()
    main.app.state.db_pool = None
    main.app.state.redis = None
    main.app.state.memory = main.InMemoryStore()
    yield


# ── Happy path ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bars_endpoint_returns_bars():
    ts1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    ts2 = datetime(2026, 8, 2, tzinfo=timezone.utc)
    pool, conn = _make_pool(fetch_return=[_row(ts1), _row(ts2)])
    main.app.state.db_pool = pool

    result = await main.get_bars_endpoint("aapl", "1d")

    assert result["ticker"] == "AAPL"
    assert result["interval"] == "1d"
    assert result["bars"] == [
        {"ts": ts1.isoformat(), "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1_000_000},
        {"ts": ts2.isoformat(), "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1_000_000},
    ]
    # No since: exactly one query (existence check reused as the result).
    assert conn.fetch.await_count == 1


@pytest.mark.asyncio
async def test_bars_endpoint_since_filters_partial():
    ts1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    ts2 = datetime(2026, 8, 2, tzinfo=timezone.utc)
    ts3 = datetime(2026, 8, 3, tzinfo=timezone.utc)
    all_bars = [_row(ts1), _row(ts2), _row(ts3)]
    filtered_bars = [_row(ts2), _row(ts3)]  # boundary row ts2 == since is included

    pool, conn = _make_pool()
    conn.fetch = AsyncMock(side_effect=[all_bars, filtered_bars])
    main.app.state.db_pool = pool

    result = await main.get_bars_endpoint("AAPL", "1d", since=ts2.isoformat())

    assert [b["ts"] for b in result["bars"]] == [ts2.isoformat(), ts3.isoformat()]
    # Existence check (unfiltered) + filtered result = two queries.
    assert conn.fetch.await_count == 2
    first_query = conn.fetch.await_args_list[0].args[0]
    second_query = conn.fetch.await_args_list[1].args[0]
    assert "ts >=" not in first_query
    assert "ts >= $3" in second_query


# ── Failure branches ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bars_endpoint_no_db_pool():
    main.app.state.db_pool = None

    with pytest.raises(HTTPException) as exc_info:
        await main.get_bars_endpoint("AAPL", "1d")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_bars_endpoint_db_query_raises():
    pool, _conn = _make_pool(fetch_side_effect=RuntimeError("connection reset"))
    main.app.state.db_pool = pool

    with pytest.raises(HTTPException) as exc_info:
        await main.get_bars_endpoint("AAPL", "1d")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_bars_endpoint_bad_ticker():
    pool, _conn = _make_pool()
    main.app.state.db_pool = pool

    with pytest.raises(HTTPException) as exc_info:
        await main.get_bars_endpoint("AAPL1", "1d")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_bars_endpoint_bad_interval():
    pool, _conn = _make_pool()
    main.app.state.db_pool = pool

    with pytest.raises(HTTPException) as exc_info:
        await main.get_bars_endpoint("AAPL", "5m")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_bars_endpoint_bad_since():
    pool, _conn = _make_pool()
    main.app.state.db_pool = pool

    with pytest.raises(HTTPException) as exc_info:
        await main.get_bars_endpoint("AAPL", "1d", since="garbage")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_bars_endpoint_not_found():
    pool, conn = _make_pool(fetch_return=[])
    main.app.state.db_pool = pool

    with pytest.raises(HTTPException) as exc_info:
        await main.get_bars_endpoint("AAPL", "1d")

    assert exc_info.value.status_code == 404
    assert conn.fetch.await_count == 1  # no second query once existence check is empty


@pytest.mark.asyncio
async def test_bars_endpoint_since_excludes_all_returns_empty():
    ts1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    future_since = datetime(2026, 9, 1, tzinfo=timezone.utc)

    pool, conn = _make_pool()
    conn.fetch = AsyncMock(side_effect=[[_row(ts1)], []])
    main.app.state.db_pool = pool

    result = await main.get_bars_endpoint("AAPL", "1d", since=future_since.isoformat())

    assert result["bars"] == []
    assert conn.fetch.await_count == 2


@pytest.mark.asyncio
async def test_bars_endpoint_naive_since_treated_as_utc():
    """A naive `since` (no tzinfo) must be compared as UTC, matching how
    `ts` is stored (TIMESTAMPTZ) and how every other caller passes it."""
    ts1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    pool, conn = _make_pool()
    conn.fetch = AsyncMock(side_effect=[[_row(ts1)], [_row(ts1)]])
    main.app.state.db_pool = pool

    await main.get_bars_endpoint("AAPL", "1d", since="2026-08-01T00:00:00")  # naive

    second_call_args = conn.fetch.await_args_list[1].args
    passed_since = second_call_args[3]
    assert passed_since.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_bars_endpoint_repeat_call_idempotent():
    ts1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    pool, conn = _make_pool(fetch_return=[_row(ts1)])
    main.app.state.db_pool = pool

    first = await main.get_bars_endpoint("AAPL", "1d")
    second = await main.get_bars_endpoint("AAPL", "1d")

    assert first == second
