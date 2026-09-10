"""Part 3.2 — the data fetchers: monitors/fred.py (this commit) and
monitors/quotes.py (next commit). No socket: FRED over respx with a no-wait
limiter, snapshots over a fake client, Redis via FakeRedis."""

import json
from datetime import date, datetime, timezone

import httpx
import pytest
import respx

from cache import MemoryCooldowns
from monitors import fred
from monitors.errors import (
    FredCoolingDown,
    FredError,
    FredNotAuthorized,
    FredNotConfigured,
    FredRateLimited,
    FredSourceWide,
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
