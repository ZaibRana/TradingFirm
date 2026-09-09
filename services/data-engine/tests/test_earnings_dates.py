"""
TradingFirm — Earnings report dates (Part 2.3, commit 1).

Covers the provider method, the Alpha Vantage client, the two pure
converters, validation against the bar store, and sync_earnings_dates'
fallback policy. Zero network (respx intercepts HTTP), zero real database
(a mocked asyncpg pool), zero yfinance (the fixture provider, or a patched
yf.Ticker).
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pandas as pd
import pytest
import respx
from fastapi.testclient import TestClient
from yfinance.exceptions import YFRateLimitError

import main
from providers.base import ProviderRateLimited
from providers.context.alphavantage_client import (
    ALPHAVANTAGE_BASE_URL,
    AlphaVantageCapped,
    AlphaVantageClient,
    AlphaVantageError,
    AlphaVantageNotConfigured,
    AlphaVantageRateLimited,
)
from providers.context.earnings import (
    HOUR_AMC,
    HOUR_BMO,
    HOUR_DMH,
    META_KEY,
    SOURCE_ALPHAVANTAGE,
    SOURCE_YFINANCE,
    earnings_events_from_av,
    earnings_events_from_df,
    sync_earnings_dates,
    validate_report_dates,
)
from providers.context.ratelimit import RateLimiter
from providers.fixture_provider import FixtureProvider

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 9, 9)
AV_URL = f"{ALPHAVANTAGE_BASE_URL}/query"
FAKE_KEY = "SECRETKEY123456"

# The four AAPL reports that fall inside the recorded daily-bar window
# (2025-09-05 → 2026-09-04); every one is an exact trading day.
AAPL_PAST_REPORTS = [date(2026, 7, 30), date(2026, 4, 30), date(2026, 1, 29), date(2025, 10, 30)]


# ── helpers ──────────────────────────────────────────────────────────────


def _make_pool(bar_dates=None):
    """Mocked asyncpg pool: fetch() returns daily bar rows for get_bars()."""
    rows = [
        {"ts": datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
         "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1000}
        for d in (bar_dates or [])
    ]
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.executemany = AsyncMock()
    pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def _frame(rows):
    """Build a get_earnings_dates()-shaped frame: tz-aware Eastern index."""
    idx = pd.DatetimeIndex([pd.Timestamp(ts, tz="America/New_York") for ts, *_ in rows],
                           name="Earnings Date")
    return pd.DataFrame(
        {
            "EPS Estimate": [r[1] for r in rows],
            "Reported EPS": [r[2] for r in rows],
            "Surprise(%)": [r[3] for r in rows],
        },
        index=idx,
    )


def _provider(df=None, exc=None):
    p = MagicMock()
    p.get_earnings_dates = AsyncMock(return_value=df, side_effect=exc)
    return p


def _client(key=FAKE_KEY):
    """Client with a wide-open limiter: the module-level alphavantage_limiter
    is the real 5-per-minute one, and sharing it across tests would make the
    suite really sleep for a minute. Its configuration is asserted in
    test_alphavantage_default_limiter_is_five_per_minute instead."""
    return AlphaVantageClient(key, limiter=RateLimiter(max_calls=10_000, window=60.0, min_gap=0.0))


def _av_client(body=None, exc=None):
    c = MagicMock(spec=AlphaVantageClient)
    c.get = AsyncMock(return_value=body, side_effect=exc)
    return c


def _av_body(dates):
    return {"symbol": "AAPL", "quarterlyEarnings": [
        {"fiscalDateEnding": "2026-06-30", "reportedDate": d.isoformat(),
         "reportedEPS": "2.02", "estimatedEPS": "1.88",
         "surprise": "0.14", "surprisePercentage": "7.4468", "reportTime": "post-market"}
        for d in dates
    ]}


# ── provider: yfinance ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_yfinance_earnings_dates_patched_call_shape():
    from providers.yfinance_provider import YFinanceProvider

    frame = _frame([("2026-07-30 16:00:00", 1.89, 2.02, 6.74)])
    ticker_obj = MagicMock()
    ticker_obj.get_earnings_dates = MagicMock(return_value=frame)
    with patch("providers.yfinance_provider.yf.Ticker", return_value=ticker_obj) as tk, \
         patch("providers.yfinance_provider.time.sleep"):
        provider = YFinanceProvider()
        out = await provider.get_earnings_dates("AAPL")

    assert out is not None and len(out) == 1
    tk.assert_called_once_with("AAPL")
    ticker_obj.get_earnings_dates.assert_called_once_with(limit=12)
    assert "session" not in ticker_obj.get_earnings_dates.call_args.kwargs


@pytest.mark.asyncio
async def test_yfinance_rate_limited_no_fallback():
    """A rate limit must never look like 'no data': it raises, and the
    sync then refuses to call the fallback."""
    from providers.yfinance_provider import YFinanceProvider

    ticker_obj = MagicMock()
    ticker_obj.get_earnings_dates = MagicMock(side_effect=YFRateLimitError())
    with patch("providers.yfinance_provider.yf.Ticker", return_value=ticker_obj), \
         patch("providers.yfinance_provider.time.sleep"):
        provider = YFinanceProvider()
        with pytest.raises(ProviderRateLimited):
            await provider.get_earnings_dates("AAPL")

    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(_av_body(AAPL_PAST_REPORTS))
    result = await sync_earnings_dates(
        _provider(exc=ProviderRateLimited("throttled")), av, "AAPL", pool, today=TODAY
    )
    assert result == {"source": None, "stored": 0, "dropped": 0, "reason": "rate_limited"}
    av.get.assert_not_called()
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_yfinance_generic_exception_returns_none():
    from providers.yfinance_provider import YFinanceProvider

    ticker_obj = MagicMock()
    ticker_obj.get_earnings_dates = MagicMock(side_effect=ValueError("boom"))
    with patch("providers.yfinance_provider.yf.Ticker", return_value=ticker_obj), \
         patch("providers.yfinance_provider.time.sleep"):
        assert await YFinanceProvider().get_earnings_dates("AAPL") is None


# ── provider: fixtures ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fixture_provider_earnings_dates_roundtrip():
    df = await FixtureProvider().get_earnings_dates("AAPL")
    assert df is not None and len(df) == 25
    assert list(df.columns) == ["EPS Estimate", "Reported EPS", "Surprise(%)"]
    assert str(df.index.tz) == "America/New_York"


@pytest.mark.asyncio
async def test_fixture_provider_earnings_dates_null_is_none():
    """SPY.json holds the literal null: recorded, and yfinance has nothing."""
    assert (FIXTURES / "earnings_dates" / "SPY.json").read_text().strip() == "null"
    assert await FixtureProvider().get_earnings_dates("SPY") is None


@pytest.mark.asyncio
async def test_fixture_provider_earnings_dates_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        await FixtureProvider().get_earnings_dates("NOSUCH")


# ── Alpha Vantage client ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alphavantage_not_configured_no_http():
    with respx.mock:
        route = respx.get(AV_URL).mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(AlphaVantageNotConfigured):
            await _client("").get("EARNINGS", "AAPL")
        assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_information_body_is_down():
    """The free daily cap arrives as HTTP 200 with an Information body."""
    respx.get(AV_URL).mock(return_value=httpx.Response(
        200, json={"Information": "rate limit is 25 requests per day"}))
    with pytest.raises(AlphaVantageCapped):
        await _client().get("EARNINGS", "AAPL")


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_note_body_is_rate_limited():
    respx.get(AV_URL).mock(return_value=httpx.Response(
        200, json={"Note": "5 calls per minute"}))
    with pytest.raises(AlphaVantageRateLimited):
        await _client().get("EARNINGS", "AAPL")


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_empty_body_is_empty():
    """An unknown symbol answers {} — empty, not an error and not 'absent keys'."""
    respx.get(AV_URL).mock(return_value=httpx.Response(200, json={}))
    body = await _client().get("EARNINGS", "ZZZZ")
    assert body == {}
    assert earnings_events_from_av("ZZZZ", body) == []


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_error_message_body():
    respx.get(AV_URL).mock(return_value=httpx.Response(
        200, json={"Error Message": "Invalid API call"}))
    with pytest.raises(AlphaVantageError):
        await _client().get("EARNINGS", "AAPL")


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_bad_json():
    respx.get(AV_URL).mock(return_value=httpx.Response(200, text="<html>nope"))
    with pytest.raises(AlphaVantageError):
        await _client().get("EARNINGS", "AAPL")


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_server_error():
    respx.get(AV_URL).mock(return_value=httpx.Response(503, text="upstream down"))
    with pytest.raises(AlphaVantageError):
        await _client().get("EARNINGS", "AAPL")


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_timeout():
    respx.get(AV_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(AlphaVantageError):
        await _client().get("EARNINGS", "AAPL")


def test_alphavantage_default_limiter_is_five_per_minute():
    """The shipped client shares one 5-per-minute window (the free cap)."""
    from providers.context import ratelimit

    assert AlphaVantageClient("k").limiter is ratelimit.alphavantage_limiter
    assert ratelimit.alphavantage_limiter.max_calls == 5
    assert ratelimit.alphavantage_limiter.window == 60.0


@pytest.mark.asyncio
async def test_alphavantage_limiter_caps_rate():
    """5 calls go straight through; the 6th waits for the window."""
    now, slept = [0.0], []

    async def fake_sleep(s):
        slept.append(s)
        now[0] += s

    limiter = RateLimiter(max_calls=5, window=60.0, min_gap=0.0,
                          clock=lambda: now[0], sleep=fake_sleep)
    for _ in range(5):
        assert await limiter.acquire() == 0
    assert not slept
    waited = await limiter.acquire()
    assert waited == pytest.approx(60.0)


@pytest.mark.asyncio
@respx.mock
async def test_alphavantage_client_sends_key_as_param_and_never_logs_url(caplog):
    """
    Decision 13: the key rides in `params` (Alpha Vantage has no header
    form) and must never reach a log record at any level, including when
    httpx raises. Exercises a 4xx and a transport error.
    """
    caplog.set_level(logging.DEBUG)

    ok = respx.get(AV_URL).mock(return_value=httpx.Response(200, json={"symbol": "AAPL"}))
    client = _client()
    await client.get("EARNINGS", "AAPL")

    request = ok.calls.last.request
    assert request.url.params["apikey"] == FAKE_KEY
    assert request.url.params["function"] == "EARNINGS"
    assert request.url.params["symbol"] == "AAPL"

    respx.get(AV_URL).mock(return_value=httpx.Response(404, text="nope"))
    with pytest.raises(AlphaVantageError) as e4xx:
        await client.get("EARNINGS", "AAPL")

    respx.get(AV_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(AlphaVantageError) as etransport:
        await client.get("EARNINGS", "AAPL")

    for err in (e4xx.value, etransport.value):
        assert FAKE_KEY not in str(err)
        assert "alphavantage.co" not in str(err)
        assert err.__cause__ is None          # `from None`: no chained httpx repr
        assert "EARNINGS" in str(err) and "AAPL" in str(err)

    for record in caplog.records:
        assert FAKE_KEY not in record.getMessage()
        assert FAKE_KEY not in str(record.args or "")
    assert logging.getLogger("httpx").level == logging.WARNING


# ── converters ───────────────────────────────────────────────────────────


def test_earnings_events_from_df_fixture_shape():
    df = pd.read_json(FIXTURES / "earnings_dates" / "AAPL.json", orient="table")
    rows = earnings_events_from_df("AAPL", df)

    assert len(rows) == 25
    row = next(r for r in rows if r["event_at"].date() == date(2026, 7, 30))
    assert row["ticker"] == "AAPL"
    assert row["event_type"] == "earnings"
    assert row["event_at"] == datetime(2026, 7, 30, tzinfo=timezone.utc)
    assert row["meta"][META_KEY] == {
        "source": SOURCE_YFINANCE, "validated": False, "hour": HOUR_AMC,
        "epsEstimate": pytest.approx(1.89), "epsReported": pytest.approx(2.02),
        "surprisePct": pytest.approx(6.74),
    }


def test_earnings_events_hour_from_eastern_time():
    df = _frame([
        ("2026-07-30 16:00:00", 1.0, 1.1, 1.0),   # after the close
        ("2026-04-30 07:00:00", 1.0, 1.1, 1.0),   # before the open
        ("2026-01-29 12:00:00", 1.0, 1.1, 1.0),   # during market hours
    ])
    hours = [r["meta"][META_KEY]["hour"] for r in earnings_events_from_df("AAPL", df)]
    assert hours == [HOUR_AMC, HOUR_BMO, HOUR_DMH]


def test_earnings_events_from_df_absent_columns(caplog):
    caplog.set_level(logging.WARNING)
    df = _frame([("2026-07-30 16:00:00", 1.0, 1.1, 1.0)]).drop(columns=["Surprise(%)"])
    rows = earnings_events_from_df("AAPL", df)
    assert len(rows) == 1
    assert rows[0]["meta"][META_KEY]["surprisePct"] is None
    assert rows[0]["meta"][META_KEY]["epsReported"] == pytest.approx(1.1)
    assert "Surprise(%)" in caplog.text


def test_earnings_events_from_df_drops_nat():
    """A NaT index entry drops its row; a NaN cell becomes JSON null."""
    df = _frame([("2026-07-30 16:00:00", 1.98, float("nan"), float("nan"))])
    df2 = pd.DataFrame(df.values, columns=df.columns,
                       index=pd.DatetimeIndex([pd.NaT], name="Earnings Date"))
    assert earnings_events_from_df("AAPL", df2) == []

    rows = earnings_events_from_df("AAPL", df)
    assert len(rows) == 1
    meta = rows[0]["meta"][META_KEY]
    assert meta["epsReported"] is None and meta["surprisePct"] is None
    assert json.loads(json.dumps(meta))["epsReported"] is None


def test_earnings_events_from_df_empty_is_empty():
    assert earnings_events_from_df("AAPL", None) == []
    assert earnings_events_from_df("AAPL", pd.DataFrame()) == []


def test_earnings_events_from_av_fixture_shape():
    body = json.loads((FIXTURES / "alphavantage" / "AAPL_earnings.json").read_text())
    rows = earnings_events_from_av("AAPL", body)

    assert len(rows) == 12
    row = next(r for r in rows if r["event_at"].date() == date(2026, 7, 30))
    assert row["event_at"] == datetime(2026, 7, 30, tzinfo=timezone.utc)
    meta = row["meta"][META_KEY]
    assert meta["source"] == SOURCE_ALPHAVANTAGE
    assert meta["hour"] == HOUR_AMC
    assert meta["epsReported"] == pytest.approx(2.02)   # "2.02" string → float
    assert meta["epsEstimate"] == pytest.approx(1.88)
    assert meta["surprisePct"] == pytest.approx(7.4468)


def test_earnings_events_from_av_report_time_to_hour():
    body = {"quarterlyEarnings": [
        {"reportedDate": "2026-07-30", "reportTime": "post-market"},
        {"reportedDate": "2026-04-30", "reportTime": "pre-market"},
        {"reportedDate": "2026-01-29", "reportTime": "unknown"},
        {"reportedDate": "2025-10-30"},
    ]}
    hours = [r["meta"][META_KEY]["hour"] for r in earnings_events_from_av("AAPL", body)]
    assert hours == [HOUR_AMC, HOUR_BMO, None, None]


def test_earnings_events_from_av_absent_keys():
    body = {"quarterlyEarnings": [{"reportedDate": "2026-07-30"}]}
    meta = earnings_events_from_av("AAPL", body)[0]["meta"][META_KEY]
    assert meta["epsEstimate"] is None
    assert meta["epsReported"] is None
    assert meta["surprisePct"] is None


def test_earnings_events_from_av_drops_bad_dates():
    body = {"quarterlyEarnings": [
        {"reportedDate": "not-a-date"},
        {"reportedDate": ""},
        {},
        "junk",
        {"reportedDate": "2026-07-30", "reportedEPS": "None"},
    ]}
    rows = earnings_events_from_av("AAPL", body)
    assert len(rows) == 1
    assert rows[0]["meta"][META_KEY]["epsReported"] is None   # "None" → null


def test_earnings_events_from_av_non_dict_body():
    assert earnings_events_from_av("AAPL", None) == []
    assert earnings_events_from_av("AAPL", []) == []
    assert earnings_events_from_av("AAPL", {"quarterlyEarnings": "nope"}) == []


def test_earnings_events_key_collides_with_calendar_events():
    """Both writers must land on the same PK so meta merges instead of
    creating a second row for one report."""
    from providers.context.finnhub import calendar_events

    finnhub_row = calendar_events("AAPL", [{"date": "2026-07-30", "hour": "amc"}])[0]
    av_row = earnings_events_from_av("AAPL", _av_body([date(2026, 7, 30)]))[0]
    yf_row = earnings_events_from_df("AAPL", _frame([("2026-07-30 16:00:00", 1.0, 1.0, 1.0)]))[0]

    key = lambda r: (r["ticker"], r["event_type"], r["event_at"])  # noqa: E731
    assert key(finnhub_row) == key(av_row) == key(yf_row)
    assert set(finnhub_row["meta"]) == {"calendar"}
    assert set(av_row["meta"]) == {META_KEY}


# ── validation ───────────────────────────────────────────────────────────


def test_validate_dates_accepts_bar_date_and_adjacent():
    bar_dates = {date(2026, 7, 30), date(2026, 4, 30)}
    rows = earnings_events_from_df("AAPL", _frame([
        ("2026-07-30 16:00:00", 1.0, 1.0, 1.0),   # exact bar date
        ("2026-04-29 16:00:00", 1.0, 1.0, 1.0),   # one day before a bar
    ]))
    kept, dropped, out_of_range = validate_report_dates(rows, bar_dates, TODAY)
    assert len(kept) == 2 and dropped == 0 and out_of_range == 0
    assert all(r["meta"][META_KEY]["validated"] for r in kept)


def test_validate_dates_drops_non_trading_dates():
    bar_dates = {date(2026, 7, 30), date(2026, 8, 20)}
    rows = earnings_events_from_df("AAPL", _frame([("2026-08-05 16:00:00", 1.0, 1.0, 1.0)]))
    kept, dropped, out_of_range = validate_report_dates(rows, bar_dates, TODAY)
    assert kept == [] and dropped == 1 and out_of_range == 0


def test_validate_dates_keeps_future_unvalidated():
    bar_dates = {date(2026, 7, 30)}
    rows = earnings_events_from_df("AAPL", _frame([("2026-10-29 16:00:00", 1.0, 1.0, 1.0)]))
    kept, dropped, out_of_range = validate_report_dates(rows, bar_dates, TODAY)
    assert len(kept) == 1 and dropped == 0 and out_of_range == 0
    assert kept[0]["meta"][META_KEY]["validated"] is False


def test_validate_dates_out_of_range_not_counted_as_dropped():
    """The live yfinance feed reaches ~6 years back against a 2-year store.
    Rows older than the store are unexplainable, not a quality problem."""
    bar_dates = {date(2026, 7, 30)}
    rows = earnings_events_from_df("AAPL", _frame([("2020-10-29 16:00:00", 1.0, 1.0, 1.0)]))
    kept, dropped, out_of_range = validate_report_dates(rows, bar_dates, TODAY)
    assert kept == [] and dropped == 0 and out_of_range == 1


def test_validate_dates_empty_bar_store():
    rows = earnings_events_from_df("AAPL", _frame([("2026-07-30 16:00:00", 1.0, 1.0, 1.0)]))
    assert validate_report_dates(rows, set(), TODAY) == ([], 0, 1)


# ── sync: source selection and fallback policy ───────────────────────────


@pytest.mark.asyncio
async def test_sync_primary_ok_no_fallback_call():
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(_av_body(AAPL_PAST_REPORTS))
    df = _frame([(f"{d.isoformat()} 16:00:00", 1.0, 1.1, 5.0) for d in AAPL_PAST_REPORTS])

    result = await sync_earnings_dates(_provider(df), av, "AAPL", pool, today=TODAY)

    assert result == {"source": SOURCE_YFINANCE, "stored": 4, "dropped": 0, "reason": None}
    av.get.assert_not_called()


@pytest.mark.asyncio
async def test_sync_stores_source_and_validated():
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    df = _frame([("2026-07-30 16:00:00", 1.89, 2.02, 6.74)])

    await sync_earnings_dates(_provider(df), _av_client({}), "AAPL", pool, today=TODAY)

    conn.executemany.assert_awaited_once()
    query, records = conn.executemany.await_args.args
    assert "INSERT INTO data_engine.events" in query
    ticker, event_type, event_at, meta_json = records[0]
    assert (ticker, event_type) == ("AAPL", "earnings")
    assert event_at == datetime(2026, 7, 30, tzinfo=timezone.utc)
    meta = json.loads(meta_json)
    assert set(meta) == {META_KEY}
    assert meta[META_KEY]["source"] == SOURCE_YFINANCE
    assert meta[META_KEY]["validated"] is True
    assert meta[META_KEY]["hour"] == HOUR_AMC


@pytest.mark.asyncio
async def test_sync_without_bar_dates_no_fallback_call():
    """An empty bar store is our failure, not the source's: storing is
    impossible, so the Alpha Vantage call would be wasted."""
    pool, conn = _make_pool([])
    provider, av = _provider(_frame([("2026-07-30 16:00:00", 1.0, 1.0, 1.0)])), _av_client({})

    result = await sync_earnings_dates(provider, av, "AAPL", pool, today=TODAY)

    assert result == {"source": None, "stored": 0, "dropped": 0, "reason": "no_bars"}
    av.get.assert_not_called()
    provider.get_earnings_dates.assert_not_called()
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_primary_none_triggers_fallback():
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(_av_body(AAPL_PAST_REPORTS))

    result = await sync_earnings_dates(_provider(None), av, "SPY", pool, today=TODAY)

    assert result["source"] == SOURCE_ALPHAVANTAGE
    assert result["stored"] == 4 and result["reason"] is None
    av.get.assert_awaited_once_with("EARNINGS", "SPY")


@pytest.mark.asyncio
async def test_yfinance_zero_rows_trigger_fallback():
    pool, _ = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(_av_body(AAPL_PAST_REPORTS))
    empty = _frame([("2026-07-30 16:00:00", 1.0, 1.0, 1.0)]).iloc[0:0]

    result = await sync_earnings_dates(_provider(empty), av, "AAPL", pool, today=TODAY)

    assert result["source"] == SOURCE_ALPHAVANTAGE
    av.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_yfinance_future_only_rows_trigger_fallback():
    """A fresh IPO: the feed carries only the upcoming report. The future
    row is still stored (6.4 needs it), but the source is the fallback."""
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(_av_body(AAPL_PAST_REPORTS))
    future = _frame([("2026-10-29 16:00:00", 1.98, float("nan"), float("nan"))])

    result = await sync_earnings_dates(_provider(future), av, "AAPL", pool, today=TODAY)

    assert result["source"] == SOURCE_ALPHAVANTAGE
    assert result["stored"] == 5          # 1 future (yfinance) + 4 past (AV)
    av.get.assert_awaited_once()
    _, records = conn.executemany.await_args.args
    sources = {json.loads(m)[META_KEY]["source"] for *_, m in records}
    assert sources == {SOURCE_YFINANCE, SOURCE_ALPHAVANTAGE}


@pytest.mark.asyncio
async def test_primary_raises_triggers_fallback():
    pool, _ = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(_av_body(AAPL_PAST_REPORTS))

    result = await sync_earnings_dates(
        _provider(exc=RuntimeError("upstream broke")), av, "AAPL", pool, today=TODAY
    )

    assert result["source"] == SOURCE_ALPHAVANTAGE
    av.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_primary_all_invalid_triggers_fallback():
    """Every primary date lands on a non-trading day inside the window."""
    pool, _ = _make_pool([date(2026, 7, 30), date(2026, 8, 20)])
    av = _av_client(_av_body([date(2026, 7, 30)]))
    df = _frame([("2026-08-05 16:00:00", 1.0, 1.0, 1.0)])

    result = await sync_earnings_dates(_provider(df), av, "AAPL", pool, today=TODAY)

    assert result["source"] == SOURCE_ALPHAVANTAGE
    assert result["dropped"] == 1
    av.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_primary_empty_fallback_works():
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(_av_body(AAPL_PAST_REPORTS))

    result = await sync_earnings_dates(_provider(None), av, "AAPL", pool, today=TODAY)

    assert result == {"source": SOURCE_ALPHAVANTAGE, "stored": 4, "dropped": 0, "reason": None}
    _, records = conn.executemany.await_args.args
    assert all(json.loads(m)[META_KEY]["source"] == SOURCE_ALPHAVANTAGE for *_, m in records)
    assert all(json.loads(m)[META_KEY]["validated"] for *_, m in records)


@pytest.mark.asyncio
async def test_fallback_also_empty():
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client({})

    result = await sync_earnings_dates(_provider(None), av, "AAPL", pool, today=TODAY)

    assert result == {"source": None, "stored": 0, "dropped": 0, "reason": "down"}
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("av_error", [
    AlphaVantageCapped("daily cap"),
    AlphaVantageRateLimited("per-minute"),
    AlphaVantageError("rejected"),
    AlphaVantageNotConfigured("no key"),
])
async def test_both_down(av_error):
    """Primary has nothing, fallback unusable: one shape, reason 'down'."""
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    av = _av_client(exc=av_error)

    result = await sync_earnings_dates(_provider(None), av, "AAPL", pool, today=TODAY)

    assert result == {"source": None, "stored": 0, "dropped": 0, "reason": "down"}
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_both_down_without_av_client():
    pool, _ = _make_pool(AAPL_PAST_REPORTS)
    result = await sync_earnings_dates(_provider(None), None, "AAPL", pool, today=TODAY)
    assert result["reason"] == "down" and result["source"] is None


@pytest.mark.asyncio
async def test_sync_rejects_bad_ticker():
    pool, _ = _make_pool(AAPL_PAST_REPORTS)
    with pytest.raises(ValueError):
        await sync_earnings_dates(_provider(None), None, "AAPL1", pool, today=TODAY)


# ── refresh wiring ───────────────────────────────────────────────────────


@pytest.fixture
def refresh_app():
    """app.state wired for the refresh endpoint: fixture provider, mocked
    pool, no Redis, no Alpha Vantage."""
    main.app.state.provider = FixtureProvider()
    main.app.state.redis = None
    main.app.state.memory = main.InMemoryStore()
    main.app.state.av_client = None
    pool, conn = _make_pool(AAPL_PAST_REPORTS)
    main.app.state.db_pool = pool
    yield pool, conn
    main.app.state.db_pool = None


def test_refresh_stores_earnings_dates(refresh_app):
    """End to end through the app: the response carries the earnings step's
    one-shape result and the events insert really ran."""
    pool, conn = refresh_app
    client = TestClient(main.app)   # no context manager: lifespan stays off

    response = client.post("/stock/AAPL/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AAPL"
    assert body["dailyBars"] == 252
    earnings = body["earningsDates"]
    assert set(earnings) == {"source", "stored", "dropped", "reason"}
    assert earnings["source"] == "yfinance"
    assert earnings["reason"] is None
    assert earnings["stored"] >= len(AAPL_PAST_REPORTS)

    queries = [c.args[0] for c in conn.executemany.await_args_list]
    assert any("INSERT INTO data_engine.events" in q for q in queries)
    assert sum("INSERT INTO data_engine.ohlcv_bars" in q for q in queries) == 2


def test_refresh_earnings_failure_does_not_fail_refresh(refresh_app):
    """Bars are the product: a broken earnings step must not lose them."""
    pool, conn = refresh_app
    with patch("providers.context.earnings.sync_earnings_dates",
               side_effect=RuntimeError("earnings exploded")):
        response = TestClient(main.app).post("/stock/AAPL/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["dailyBars"] == 252 and body["hourlyBars"] > 0
    assert body["earningsDates"] == {
        "source": None, "stored": 0, "dropped": 0, "reason": "error"}
