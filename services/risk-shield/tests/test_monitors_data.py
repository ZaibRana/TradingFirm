"""Part 3.2 — the data fetchers: monitors/fred.py and monitors/quotes.py.
No socket: FRED over respx with a no-wait limiter, snapshots over a fake
client, yf.download patched with a synthetic 1.5.1-layout frame, Redis via
FakeRedis."""

import asyncio
import json
import logging
import time
from datetime import date, datetime, timezone

import httpx
import numpy as np
import pandas as pd
import pytest
import respx
from yfinance.exceptions import YFRateLimitError

from cache import MemoryCooldowns
from monitors import fred, quotes
from monitors.errors import (
    FredCoolingDown,
    FredError,
    FredNotAuthorized,
    FredNotConfigured,
    FredRateLimited,
    FredSourceWide,
    QuotesCoolingDown,
    QuotesError,
    QuotesRateLimited,
)
from monitors.fred_client import FredClient
from tests.fake_redis import FakeRedis

# ── FRED helpers ─────────────────────────────────────────────────

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
NOW = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
KEY_DGS10 = "tf:risk:cache:fred:DGS10"
COOL_FRED = "tf:risk:cooldown:FRED"


def _now():
    return NOW


class NoWaitLimiter:
    async def acquire(self):
        return 0.0


def _real_client(key="k" * 32):
    return FredClient(key, limiter=NoWaitLimiter(), today=lambda: date(2026, 9, 10))


def _obs(*pairs):
    return {"observations": [{"date": d, "value": v} for d, v in pairs]}


FULL = _obs(("2026-09-04", "4.10"), ("2026-09-07", "."), ("2026-09-08", "4.12"))


class FakeFredClient:
    """Stands in for FredClient in snapshot tests: raises per series."""

    def __init__(self, raises=None, body=None):
        self.raises = raises or {}
        self.body = body or FULL
        self.calls = []

    def observation_start(self):
        return "2024-07-02"

    async def observations(self, sid):
        self.calls.append(sid)
        if sid in self.raises:
            raise self.raises[sid]
        return self.body


def _assert_no_none(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            _assert_no_none(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_none(v)
    else:
        assert obj is not None


# ── FRED: happy path ─────────────────────────────────────────────

def test_fred_series_are_the_plans_8():
    assert fred.FRED_SERIES == (
        "VIXCLS", "DGS10", "DGS2", "T10Y2Y", "DFF", "DCOILWTICO", "CPIAUCSL", "UNRATE"
    )
    assert all(fred.normalize_series(s.lower()) == s for s in fred.FRED_SERIES)


@pytest.mark.asyncio
async def test_fred_first_call_fetches_and_caches():
    r = FakeRedis()
    with respx.mock() as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json=FULL))
        body, from_cache = await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10", now=_now)
    assert from_cache is False
    assert body == {
        "seriesId": "DGS10",
        "asOf": NOW.isoformat(),
        "observationStart": "2024-07-02",
        "observations": [{"date": "2026-09-04", "value": 4.10}, {"date": "2026-09-08", "value": 4.12}],
        "dropped": 1,
        "reason": None,
    }
    assert json.loads(r.store[KEY_DGS10]) == body
    assert r.ttls[KEY_DGS10] == 21600
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_fred_second_call_is_hit():
    r = FakeRedis()
    with respx.mock() as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json=FULL))
        await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10", now=_now)
        body, from_cache = await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10", now=_now)
    assert from_cache is True
    assert body["reason"] is None
    assert route.call_count == 1


# ── FRED: failure branches ───────────────────────────────────────

@pytest.mark.asyncio
async def test_fred_unknown_series_rejected():
    with respx.mock(assert_all_called=False) as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json=FULL))
        for bad in ("GDP", "", None):
            with pytest.raises(ValueError):
                await fred.get_series(FakeRedis(), MemoryCooldowns(), _real_client(), bad)
    assert route.call_count == 0


@pytest.mark.asyncio
async def test_fred_series_id_normalized_to_one_key():
    r = FakeRedis()
    with respx.mock() as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json=FULL))
        await fred.get_series(r, MemoryCooldowns(), _real_client(), " dgs10 ", now=_now)
        _, from_cache = await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10", now=_now)
    assert from_cache is True
    assert route.call_count == 1
    assert [k for k in r.store if k.startswith("tf:risk:cache:fred:")] == [KEY_DGS10]


@pytest.mark.asyncio
async def test_fred_cooldown_skips_http():
    r = FakeRedis()
    r.store[COOL_FRED] = "1"
    r.ttls[COOL_FRED] = 500
    with respx.mock(assert_all_called=False) as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json=FULL))
        with pytest.raises(FredCoolingDown) as exc:
            await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10")
    assert exc.value.remaining == 500
    assert route.call_count == 0
    assert KEY_DGS10 not in r.store


@pytest.mark.asyncio
async def test_fred_cooldown_is_source_wide():
    r = FakeRedis()
    responses = {
        "DGS10": httpx.Response(429, json={"error_code": 429, "error_message": "Too Many Requests"}),
        "VIXCLS": httpx.Response(200, json=FULL),
    }
    with respx.mock(assert_all_called=False) as m:
        route = m.get(FRED_URL).mock(side_effect=lambda req: responses[req.url.params["series_id"]])
        with pytest.raises(FredRateLimited):
            await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10")
        with pytest.raises(FredCoolingDown):
            await fred.get_series(r, MemoryCooldowns(), _real_client(), "VIXCLS")
    assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 423])
async def test_fred_rate_limit_starts_cooldown_caches_nothing(status):
    r = FakeRedis()
    with respx.mock() as m:
        m.get(FRED_URL).mock(return_value=httpx.Response(status, json={"error_code": status, "error_message": "x"}))
        with pytest.raises(FredRateLimited):
            await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10")
    assert r.ttls[COOL_FRED] == 900
    assert KEY_DGS10 not in r.store


@pytest.mark.asyncio
async def test_fred_not_authorized_starts_long_cooldown():
    r = FakeRedis()
    with respx.mock() as m:
        m.get(FRED_URL).mock(return_value=httpx.Response(400, json={
            "error_code": 400,
            "error_message": "Bad Request.  The value for variable api_key is not registered.",
        }))
        with pytest.raises(FredNotAuthorized):
            await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10")
    assert r.ttls[COOL_FRED] == 3600
    assert KEY_DGS10 not in r.store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(404, json={"error_code": 404, "error_message": "Not Found"}),
        httpx.Response(200, json={"unexpected": True}),
    ],
    ids=["5xx", "404", "shape"],
)
async def test_fred_error_no_cooldown_caches_nothing(response):
    r = FakeRedis()
    with respx.mock() as m:
        m.get(FRED_URL).mock(return_value=response)
        with pytest.raises(FredError) as exc:
            await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10")
    assert not isinstance(exc.value, FredSourceWide)
    assert COOL_FRED not in r.store
    assert KEY_DGS10 not in r.store


@pytest.mark.asyncio
async def test_fred_unconfigured_no_cooldown_no_http():
    r = FakeRedis()
    with respx.mock(assert_all_called=False) as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json=FULL))
        with pytest.raises(FredNotConfigured):
            await fred.get_series(r, MemoryCooldowns(), _real_client(key=""), "DGS10")
    assert route.call_count == 0
    assert COOL_FRED not in r.store
    assert KEY_DGS10 not in r.store


@pytest.mark.asyncio
async def test_fred_empty_body_cached_120s_not_normal_ttl():
    r = FakeRedis()
    with respx.mock() as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json={"observations": []}))
        body, from_cache = await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10", now=_now)
        assert body["reason"] == "empty" and body["observations"] == []
        assert r.ttls[KEY_DGS10] == 120
        assert r.ttls[KEY_DGS10] != 21600
        body2, from_cache2 = await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10", now=_now)
    assert from_cache is False and from_cache2 is True
    assert body2 == body
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_fred_missing_values_dropped_and_counted():
    raw = {"observations": [
        {"date": "2026-09-01", "value": "."},
        {"date": "2026-09-02", "value": "abc"},
        {"date": "2026-09-03", "value": "nan"},
        "junk",
        {"value": "4.0"},
        {"date": "2026-09-04", "value": "4.1"},
    ]}
    body = fred.observations_to_envelope("DGS10", raw, "2024-07-02", NOW)
    assert body["observations"] == [{"date": "2026-09-04", "value": 4.1}]
    assert body["dropped"] == 5
    assert body["reason"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    ["{}", json.dumps({"seriesId": "VIXCLS", "asOf": "x", "observationStart": "x",
                       "observations": [], "dropped": 0, "reason": None})],
    ids=["bare_empty_dict", "other_series_body"],
)
async def test_fred_wrong_shape_cache_is_miss(stored):
    r = FakeRedis()
    r.store[KEY_DGS10] = stored
    with respx.mock() as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(200, json=FULL))
        body, from_cache = await fred.get_series(r, MemoryCooldowns(), _real_client(), "DGS10", now=_now)
    assert from_cache is False
    assert body["seriesId"] == "DGS10"
    assert json.loads(r.store[KEY_DGS10])["seriesId"] == "DGS10"
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_fred_without_redis_uses_memory_cooldown():
    memory = MemoryCooldowns()
    with respx.mock(assert_all_called=False) as m:
        route = m.get(FRED_URL).mock(return_value=httpx.Response(429, json={"error_code": 429, "error_message": "x"}))
        with pytest.raises(FredRateLimited):
            await fred.get_series(None, memory, _real_client(), "DGS10")
        with pytest.raises(FredCoolingDown):
            await fred.get_series(None, memory, _real_client(), "VIXCLS")
    assert route.call_count == 1


# ── FRED: snapshot ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fred_snapshot_all_series_ok():
    r = FakeRedis()
    client = FakeFredClient()
    out = await fred.fred_snapshot(r, MemoryCooldowns(), client, now=_now)
    assert list(out) == list(fred.FRED_SERIES)
    assert all(e["status"] == "ok" and e["cached"] is False and e["dropped"] == 1 for e in out.values())
    _assert_no_none(out)
    again = await fred.fred_snapshot(r, MemoryCooldowns(), client, now=_now)
    assert all(e["cached"] is True for e in again.values())
    assert len(client.calls) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc, status",
    [
        (FredCoolingDown(30), "cooldown"),
        (FredRateLimited("DGS2: HTTP 429"), "rate_limited"),
        (FredNotAuthorized("DGS2: HTTP 400 (api_key rejected)"), "not_authorized"),
        (FredNotConfigured("DGS2: FRED_API_KEY is not set"), "unconfigured"),
    ],
    ids=["cooldown", "rate_limited", "not_authorized", "unconfigured"],
)
async def test_fred_snapshot_stops_on_source_wide_state(exc, status):
    client = FakeFredClient(raises={"DGS2": exc})
    out = await fred.fred_snapshot(FakeRedis(), MemoryCooldowns(), client, now=_now)
    assert client.calls == ["VIXCLS", "DGS10", "DGS2"]
    assert out["VIXCLS"]["status"] == "ok" and out["DGS10"]["status"] == "ok"
    assert out["DGS2"] == {"status": status, "cached": False, "observations": [], "dropped": 0}
    for sid in fred.FRED_SERIES[3:]:
        assert out[sid] == {"status": "skipped", "cached": False, "observations": [], "dropped": 0}
    _assert_no_none(out)


@pytest.mark.asyncio
async def test_fred_snapshot_continues_past_series_error():
    client = FakeFredClient(raises={"DGS2": FredError("DGS2: HTTP 404")})
    out = await fred.fred_snapshot(FakeRedis(), MemoryCooldowns(), client, now=_now)
    assert client.calls == list(fred.FRED_SERIES)
    assert out["DGS2"]["status"] == "error"
    assert out["DGS2"]["observations"] == []
    assert all(out[s]["status"] == "ok" for s in fred.FRED_SERIES if s != "DGS2")
    _assert_no_none(out)


# ── Quotes helpers ───────────────────────────────────────────────

KEY_QUOTES = "tf:risk:cache:quotes"
COOL_YF = "tf:risk:cooldown:YFINANCE"
PRICE_FIELDS = ["Open", "High", "Low", "Close", "Volume"]
RATE_LIMIT_LOGS = [
    "['SPY']: YFRateLimitError('Too Many Requests. Rate limited. Try after a while.')",
    "Failed to get ticker 'SPY' reason: YFRateLimitError('Too Many Requests. Rate limited. Try after a while.')",
]


def _frame(tickers=quotes.CORE_TICKERS, rows=3, empty_for=()):
    """The 1.5.1 group_by="ticker" layout: MultiIndex (Ticker, Price). A
    failed ticker is present with all-NaN columns, as multi.py reindexes it."""
    index = pd.date_range("2026-09-04", periods=rows, freq="B", name="Date")
    columns = pd.MultiIndex.from_product([list(tickers), PRICE_FIELDS], names=["Ticker", "Price"])
    data = np.tile([100.0, 101.0, 99.0, 100.5, 1_000_000.0], (rows, len(tickers)))
    df = pd.DataFrame(data, index=index, columns=columns)
    for t in empty_for:
        df[t] = np.nan
    return df


class FakeDownload:
    """Patched over quotes.yf.download: records kwargs, optionally logs a
    yfinance record, raises, or sleeps (it runs inside to_thread)."""

    def __init__(self, frame=None, log=None, raises=None, sleep=0.0):
        self.frame = frame if frame is not None else _frame()
        self.log = log
        self.raises = raises
        self.sleep = sleep
        self.calls = []

    def __call__(self, tickers, **kwargs):
        self.calls.append((tickers, kwargs))
        if self.sleep:
            time.sleep(self.sleep)
        if self.log:
            logging.getLogger("yfinance").error(self.log)
        if self.raises is not None:
            raise self.raises
        return self.frame.copy()


@pytest.fixture
def fake_download(monkeypatch):
    def install(**kw):
        fake = FakeDownload(**kw)
        monkeypatch.setattr(quotes.yf, "download", fake)
        return fake
    return install


# ── Quotes: happy path ───────────────────────────────────────────

def test_core_tickers_are_the_plans_17():
    assert quotes.CORE_TICKERS == (
        "SPY", "QQQ", "RSP", "^VIX", "TLT", "GLD", "UUP", "XLK", "XLU",
        "XLP", "XLV", "XLY", "XLF", "ES=F", "NQ=F", "CL=F", "GC=F",
    )
    assert len(set(quotes.CORE_TICKERS)) == 17 <= 20


@pytest.mark.asyncio
async def test_quotes_first_call_downloads_and_caches(fake_download):
    fake = fake_download()
    r = FakeRedis()
    body, from_cache = await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert from_cache is False
    assert set(body) == {"asOf", "period", "interval", "tickers", "missing", "reason"}
    assert body["asOf"] == NOW.isoformat()
    assert (body["period"], body["interval"], body["reason"], body["missing"]) == ("1y", "1d", None, [])
    assert list(body["tickers"]) == list(quotes.CORE_TICKERS)
    assert body["tickers"]["SPY"] == {
        "date": ["2026-09-04", "2026-09-07", "2026-09-08"],
        "open": [100.0] * 3, "high": [101.0] * 3, "low": [99.0] * 3,
        "close": [100.5] * 3, "volume": [1_000_000] * 3,
    }
    assert json.loads(r.store[KEY_QUOTES]) == body
    assert r.ttls[KEY_QUOTES] == 300
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_quotes_second_call_is_hit(fake_download):
    fake = fake_download()
    r = FakeRedis()
    await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    _, from_cache = await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert from_cache is True
    assert len(fake.calls) == 1


# ── Quotes: failure branches ─────────────────────────────────────

@pytest.mark.asyncio
async def test_quotes_download_params_pinned(fake_download):
    fake = fake_download()
    await quotes.get_core_quotes(FakeRedis(), MemoryCooldowns(), now=_now)
    tickers, kwargs = fake.calls[0]
    assert tickers == " ".join(quotes.CORE_TICKERS)
    assert kwargs == {
        "period": "1y", "interval": "1d", "group_by": "ticker", "threads": False,
        "progress": False, "auto_adjust": True, "timeout": 5,
    }
    assert "session" not in kwargs


@pytest.mark.asyncio
async def test_quotes_cooldown_skips_download(fake_download):
    fake = fake_download()
    r = FakeRedis()
    r.store[COOL_YF] = "1"
    r.ttls[COOL_YF] = 600
    with pytest.raises(QuotesCoolingDown) as exc:
        await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert exc.value.remaining == 600
    assert fake.calls == []
    assert KEY_QUOTES not in r.store


@pytest.mark.asyncio
@pytest.mark.parametrize("message", RATE_LIMIT_LOGS, ids=["download_summary", "tz_fetch"])
async def test_quotes_rate_limit_from_yfinance_log_starts_cooldown(fake_download, message):
    fake_download(log=message)   # the frame itself looks complete
    r = FakeRedis()
    with pytest.raises(QuotesRateLimited):
        await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert r.ttls[COOL_YF] == 900
    assert KEY_QUOTES not in r.store


@pytest.mark.asyncio
async def test_quotes_raised_rate_limit_is_refusal(fake_download):
    fake_download(raises=YFRateLimitError())
    r = FakeRedis()
    with pytest.raises(QuotesRateLimited):
        await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert r.ttls[COOL_YF] == 900
    assert KEY_QUOTES not in r.store


@pytest.mark.asyncio
async def test_quotes_download_error_no_cooldown_caches_nothing(fake_download):
    fake_download(raises=RuntimeError("chart endpoint changed"))
    r = FakeRedis()
    with pytest.raises(QuotesError) as exc:
        await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert not isinstance(exc.value, QuotesRateLimited)
    assert COOL_YF not in r.store
    assert KEY_QUOTES not in r.store


@pytest.mark.asyncio
async def test_quotes_log_capture_handler_removed(fake_download):
    yf_logger = logging.getLogger("yfinance")
    before = list(yf_logger.handlers)
    fake_download()
    await quotes.get_core_quotes(FakeRedis(), MemoryCooldowns(), now=_now)
    assert yf_logger.handlers == before
    fake_download(raises=RuntimeError("boom"))
    with pytest.raises(QuotesError):
        await quotes.get_core_quotes(FakeRedis(), MemoryCooldowns(), now=_now)
    assert yf_logger.handlers == before


@pytest.mark.asyncio
async def test_quotes_empty_body_cached_120s_not_normal_ttl(fake_download):
    fake = fake_download(frame=_frame(empty_for=quotes.CORE_TICKERS))
    r = FakeRedis()
    body, from_cache = await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert body["reason"] == "empty" and body["tickers"] == {}
    assert body["missing"] == list(quotes.CORE_TICKERS)
    assert r.ttls[KEY_QUOTES] == 120
    assert r.ttls[KEY_QUOTES] != 300
    body2, from_cache2 = await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert (from_cache, from_cache2) == (False, True)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_quotes_all_empty_starts_cooldown(fake_download):
    fake_download(frame=_frame(empty_for=quotes.CORE_TICKERS))
    r = FakeRedis()
    await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert r.store[COOL_YF] == "1"
    assert r.ttls[COOL_YF] == 900
    assert r.ttls[KEY_QUOTES] == 120


@pytest.mark.asyncio
async def test_quotes_partial_lists_missing(fake_download):
    fake_download(frame=_frame(empty_for=("ES=F", "NQ=F")))
    r = FakeRedis()
    body, _ = await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert body["reason"] == "partial"
    assert body["missing"] == ["ES=F", "NQ=F"]
    assert len(body["tickers"]) == 15
    assert r.ttls[KEY_QUOTES] == 120
    assert COOL_YF not in r.store


def test_quotes_nan_rows_dropped_and_nulls():
    df = _frame(tickers=("SPY",), rows=3)
    df.loc[df.index[0], ("SPY", "Close")] = np.nan
    df.loc[df.index[1], ("SPY", "Volume")] = np.nan
    df.loc[df.index[2], ("SPY", "High")] = np.inf
    series, missing = quotes.frame_to_tickers(df, ["SPY"])
    assert missing == []
    assert series["SPY"]["date"] == ["2026-09-07", "2026-09-08"]
    assert series["SPY"]["volume"] == [None, 1_000_000]
    assert series["SPY"]["high"] == [101.0, None]
    json.dumps(series, allow_nan=False)   # nothing non-JSON survives


def test_quotes_frame_to_body_handles_flat_columns():
    flat = _frame(tickers=("SPY",), rows=2)["SPY"]
    assert not isinstance(flat.columns, pd.MultiIndex)
    series, missing = quotes.frame_to_tickers(flat, ["SPY"])
    assert missing == []
    assert series["SPY"]["close"] == [100.5, 100.5]
    multi, _ = quotes.frame_to_tickers(_frame(tickers=("SPY",), rows=2), ["SPY"])
    assert multi == series


@pytest.mark.asyncio
async def test_quotes_concurrent_misses_download_once(fake_download):
    """The thread cannot be cancelled, so there is no outer wait_for: the
    lock is held for the whole slow download and the second caller gets
    the cached body."""
    fake = fake_download(sleep=0.2)
    r = FakeRedis()
    (b1, c1), (b2, c2) = await asyncio.gather(
        quotes.get_core_quotes(r, MemoryCooldowns(), now=_now),
        quotes.get_core_quotes(r, MemoryCooldowns(), now=_now),
    )
    assert len(fake.calls) == 1
    assert sorted([c1, c2]) == [False, True]
    assert b1 == b2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        "{}",
        json.dumps({"asOf": "x", "period": "1y", "interval": "1d",
                    "tickers": {"SPY": {}}, "missing": [], "reason": None}),
    ],
    ids=["bare_empty_dict", "ticker_list_change"],
)
async def test_quotes_wrong_shape_cache_is_miss(fake_download, stored):
    fake = fake_download()
    r = FakeRedis()
    r.store[KEY_QUOTES] = stored
    body, from_cache = await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert from_cache is False
    assert len(fake.calls) == 1
    assert list(json.loads(r.store[KEY_QUOTES])["tickers"]) == list(quotes.CORE_TICKERS)


@pytest.mark.asyncio
async def test_quotes_without_redis_uses_memory_cooldown(fake_download):
    fake = fake_download(frame=_frame(empty_for=quotes.CORE_TICKERS))
    memory = MemoryCooldowns()
    body, _ = await quotes.get_core_quotes(None, memory, now=_now)
    assert body["reason"] == "empty"
    with pytest.raises(QuotesCoolingDown):
        await quotes.get_core_quotes(None, memory, now=_now)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_quotes_refuses_unexpected_yfinance_version(fake_download, monkeypatch):
    fake = fake_download()
    monkeypatch.setattr(quotes.yf, "__version__", "1.6.0")
    r = FakeRedis()
    with pytest.raises(QuotesError, match="1.6.0"):
        await quotes.get_core_quotes(r, MemoryCooldowns(), now=_now)
    assert fake.calls == []
    assert KEY_QUOTES not in r.store
