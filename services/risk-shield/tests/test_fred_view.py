"""Part 3.6a — the FRED last-known envelope and get_fred_view (spec decision
3). No socket: a fake client per series, Redis via FakeRedis. The 3.2
fetcher tests stay in test_monitors_data.py; these cover what 3.6a adds."""

import json
import logging
from datetime import datetime, timezone

import pytest

from cache import KIND_FRED_LAST, TTL_FRED_LAST_KNOWN, MemoryCooldowns, risk_key
from monitors import fred
from monitors.errors import (
    FredCoolingDown,
    FredError,
    FredNotAuthorized,
    FredNotConfigured,
    FredRateLimited,
)
from tests.fake_redis import FakeRedis

NOW = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)


def _now():
    return NOW


def _obs(*pairs):
    return {"observations": [{"date": d, "value": v} for d, v in pairs]}


FULL = _obs(("2026-09-04", "4.10"), ("2026-09-07", "."), ("2026-09-08", "4.12"))
EMPTY = _obs(("2026-09-08", "."))


class FakeFredClient:
    """Stands in for FredClient: a body per series, raises per series."""

    def __init__(self, raises=None, body=None, bodies=None):
        self.raises = raises or {}
        self.body = body or FULL
        self.bodies = bodies or {}
        self.calls = []

    def observation_start(self):
        return "2024-07-02"

    async def observations(self, sid):
        self.calls.append(sid)
        if sid in self.raises:
            raise self.raises[sid]
        return self.bodies.get(sid, self.body)


def _last_key(sid):
    return risk_key(KIND_FRED_LAST, sid)


def _envelope(sid, pairs=(("2026-08-01", 1.5), ("2026-09-01", 2.5)), reason=None):
    return {"seriesId": sid, "asOf": "2026-09-09T14:00:00+00:00", "observationStart": "2024-07-02",
            "observations": [{"date": d, "value": v} for d, v in pairs],
            "dropped": 0, "reason": reason}


def _seed_last(r, sids=fred.FRED_SERIES, **kw):
    for sid in sids:
        r.store[_last_key(sid)] = json.dumps(_envelope(sid, **kw))


SOURCE_KEYS = ("status", "source", "stale", "staleReason", "latest")


def _pick(entry):
    """The source-state keys (commit 3a); cadence keys are 3b's own rows."""
    return {k: entry[k] for k in SOURCE_KEYS}


# ── Last-known writes ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fred_full_answer_writes_last_known():
    r = FakeRedis()
    body, cached = await fred.get_series(r, MemoryCooldowns(), FakeFredClient(), "DGS10", now=_now)
    assert cached is False
    stored = json.loads(r.store[_last_key("DGS10")])
    assert stored == body
    assert stored["reason"] is None
    assert r.ttls[_last_key("DGS10")] == TTL_FRED_LAST_KNOWN == 604800
    # Written before the main key (the same order as 3.3's quotes_last).
    keys = [k for k, _, _ in r.set_calls]
    assert keys.index(_last_key("DGS10")) < keys.index("tf:risk:cache:fred:DGS10")


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [
    FakeFredClient(body=EMPTY),
    FakeFredClient(raises={"DGS10": FredError("DGS10: HTTP 500")}),
    FakeFredClient(raises={"DGS10": FredRateLimited("DGS10: HTTP 429")}),
], ids=["empty", "error", "rate_limited"])
async def test_fred_degraded_answer_never_writes_last_known(client):
    r = FakeRedis()
    try:
        await fred.get_series(r, MemoryCooldowns(), client, "DGS10", now=_now)
    except FredError:
        pass
    assert _last_key("DGS10") not in r.store
    assert all(k != _last_key("DGS10") for k, _, _ in r.set_calls)


@pytest.mark.asyncio
async def test_fred_last_known_write_failure_still_answers(caplog):
    r = FakeRedis(fail_set=True)
    with caplog.at_level(logging.WARNING, logger="monitors.fred"):
        body, cached = await fred.get_series(r, MemoryCooldowns(), FakeFredClient(), "DGS10", now=_now)
    assert body["reason"] is None and len(body["observations"]) == 2
    assert cached is False
    assert any("Last-known FRED write failed for DGS10" in rec.getMessage() for rec in caplog.records)


# ── The view ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("exc, status", [
    (FredCoolingDown(300), "cooldown"),
    (FredRateLimited("VIXCLS: HTTP 429"), "rate_limited"),
    (FredNotAuthorized("VIXCLS: HTTP 400 (api_key rejected)"), "not_authorized"),
], ids=["cooldown", "rate_limited", "not_authorized"])
async def test_fred_view_refusal_serves_last_known_stale(exc, status):
    """The 3.3 carry: a cooldown or refusal is stale data, never a raise."""
    r = FakeRedis()
    _seed_last(r)
    view = await fred.get_fred_view(r, MemoryCooldowns(), FakeFredClient(raises={"VIXCLS": exc}), now=_now)
    assert list(view) == list(fred.FRED_SERIES)
    assert _pick(view["VIXCLS"]) == {"status": status, "source": "last_known", "stale": True,
                                     "staleReason": "last_known", "latest": {"date": "2026-09-01", "value": 2.5}}
    for sid in fred.FRED_SERIES[1:]:
        assert view[sid]["status"] == "skipped"
        assert view[sid]["source"] == "last_known" and view[sid]["stale"] is True


@pytest.mark.asyncio
async def test_fred_view_series_error_serves_last_known():
    r = FakeRedis()
    _seed_last(r, sids=("DGS2",))
    client = FakeFredClient(raises={"DGS2": FredError("DGS2: HTTP 404")})
    view = await fred.get_fred_view(r, MemoryCooldowns(), client, now=_now)
    assert client.calls == list(fred.FRED_SERIES)
    assert _pick(view["DGS2"]) == {"status": "error", "source": "last_known", "stale": True,
                                   "staleReason": "last_known", "latest": {"date": "2026-09-01", "value": 2.5}}
    for sid in fred.FRED_SERIES:
        if sid != "DGS2":
            assert _pick(view[sid]) == {"status": "ok", "source": "fresh", "stale": False, "staleReason": None,
                                        "latest": {"date": "2026-09-08", "value": 4.12}}


@pytest.mark.asyncio
async def test_fred_view_empty_serves_last_known():
    r = FakeRedis()
    _seed_last(r, sids=("CPIAUCSL",))
    client = FakeFredClient(bodies={"CPIAUCSL": EMPTY})
    view = await fred.get_fred_view(r, MemoryCooldowns(), client, now=_now)
    assert view["CPIAUCSL"]["status"] == "empty"
    assert view["CPIAUCSL"]["source"] == "last_known"
    assert view["CPIAUCSL"]["staleReason"] == "last_known"
    assert view["CPIAUCSL"]["latest"] == {"date": "2026-09-01", "value": 2.5}
    # The empty answer did not overwrite the stored full envelope.
    assert json.loads(r.store[_last_key("CPIAUCSL")])["reason"] is None


@pytest.mark.asyncio
async def test_fred_view_no_last_known_is_no_data():
    """The twin: no key, nothing stored. No raise, every series says why."""
    client = FakeFredClient(raises={"VIXCLS": FredNotConfigured("VIXCLS: FRED_API_KEY is not set")})
    view = await fred.get_fred_view(FakeRedis(), MemoryCooldowns(), client, now=_now)
    assert _pick(view["VIXCLS"]) == {"status": "unconfigured", "source": "none", "stale": True,
                                     "staleReason": "no_data", "latest": None}
    assert view["VIXCLS"]["ageDays"] is None and view["VIXCLS"]["monthAgo"] is None
    assert all(view[s]["staleReason"] == "no_data" and view[s]["status"] == "skipped"
               for s in fred.FRED_SERIES[1:])


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [
    json.dumps(_envelope("DGS2")),                      # another series' id
    json.dumps(_envelope("DGS10", reason="empty")),     # a degraded envelope
    json.dumps({"seriesId": "DGS10"}),                  # not an envelope
    "not json",
], ids=["other-series", "degraded", "not-envelope", "not-json"])
async def test_fred_last_known_wrong_shape_ignored(stored, caplog):
    r = FakeRedis()
    r.store[_last_key("DGS10")] = stored
    assert await fred.read_last_known(r, "DGS10") is None
    client = FakeFredClient(raises={"DGS10": FredError("DGS10: HTTP 500")})
    view = await fred.get_fred_view(r, MemoryCooldowns(), client, now=_now)
    assert view["DGS10"]["source"] == "none" and view["DGS10"]["staleReason"] == "no_data"


@pytest.mark.asyncio
async def test_fred_view_without_redis_has_no_last_known():
    memory = MemoryCooldowns()
    memory.start("FRED")
    client = FakeFredClient()
    view = await fred.get_fred_view(None, memory, client, now=_now)
    assert client.calls == []
    assert view["VIXCLS"]["status"] == "cooldown"
    assert all(v["source"] == "none" and v["staleReason"] == "no_data" for v in view.values())


@pytest.mark.asyncio
async def test_fred_view_repeat_call_is_cached():
    r = FakeRedis()
    client = FakeFredClient()
    first = await fred.get_fred_view(r, MemoryCooldowns(), client, now=_now)
    second = await fred.get_fred_view(r, MemoryCooldowns(), client, now=_now)
    assert len(client.calls) == 8
    assert all(v["source"] == "fresh" for v in first.values())
    assert all(v["source"] == "cache" and v["stale"] is False for v in second.values())
    assert [v["latest"] for v in first.values()] == [v["latest"] for v in second.values()]


# ── Freshness by cadence (commit 3b, spec decision 4) ────────────

def test_fred_cadence_table_covers_every_series():
    """Spec 3.6a decision 4's table, set from the step 0 live check."""
    assert set(fred.FRED_CADENCE) == set(fred.FRED_SERIES)
    assert fred.FRED_CADENCE == {
        "VIXCLS": ("daily", 6), "DGS10": ("daily", 6), "DGS2": ("daily", 6),
        "T10Y2Y": ("daily", 6), "DFF": ("daily", 6), "DCOILWTICO": ("daily", 14),
        "CPIAUCSL": ("monthly", 80), "UNRATE": ("monthly", 70),
    }


def _body_ending(day):
    return _obs((day.isoformat(), "1.0"))


@pytest.mark.asyncio
@pytest.mark.parametrize("sid", fred.FRED_SERIES)
async def test_fred_freshness_by_series_cadence(sid):
    today = NOW.astimezone(fred.ET).date()
    max_age = fred.FRED_CADENCE[sid][1]
    from datetime import timedelta

    at_limit = FakeFredClient(bodies={sid: _body_ending(today - timedelta(days=max_age))})
    view = await fred.get_fred_view(FakeRedis(), MemoryCooldowns(), at_limit, now=_now)
    assert (view[sid]["ageDays"], view[sid]["stale"], view[sid]["staleReason"]) == (max_age, False, None)
    assert view[sid]["cadence"] == fred.FRED_CADENCE[sid][0] and view[sid]["maxAgeDays"] == max_age

    past = FakeFredClient(bodies={sid: _body_ending(today - timedelta(days=max_age + 1))})
    view = await fred.get_fred_view(FakeRedis(), MemoryCooldowns(), past, now=_now)
    assert (view[sid]["ageDays"], view[sid]["stale"], view[sid]["staleReason"]) == (max_age + 1, True, "age")
    assert view[sid]["status"] == "ok" and view[sid]["source"] == "fresh"


def test_fred_compact_points():
    obs = [{"date": d, "value": v} for d, v in (
        ("2025-08-01", 1.0), ("2025-09-10", 2.0), ("2025-09-12", 3.0),   # year-ago window
        ("2026-08-05", 4.0), ("2026-08-12", 5.0),                         # month-ago window (gap to 09-08)
        ("2026-09-08", 6.0),
    )]
    assert fred.compact_points(obs) == {
        "latest": {"date": "2026-09-08", "value": 6.0},
        "monthAgo": {"date": "2026-08-05", "value": 4.0},    # last on or before 2026-08-09
        "yearAgo": {"date": "2025-08-01", "value": 1.0},     # last on or before 2025-09-08
    }
    # History too short for either comparison point.
    assert fred.compact_points(obs[-2:]) == {
        "latest": {"date": "2026-09-08", "value": 6.0}, "monthAgo": None, "yearAgo": None}
    assert fred.compact_points(obs[-1:]) == {"latest": obs[-1], "monthAgo": None, "yearAgo": None}
    assert fred.compact_points([]) == {"latest": None, "monthAgo": None, "yearAgo": None}


@pytest.mark.asyncio
async def test_fred_view_never_carries_observation_arrays():
    view = await fred.get_fred_view(FakeRedis(), MemoryCooldowns(), FakeFredClient(), now=_now)
    for entry in view.values():
        assert set(entry) == {"status", "source", "stale", "staleReason", "cadence", "maxAgeDays",
                              "ageDays", "latest", "monthAgo", "yearAgo"}


@pytest.mark.asyncio
async def test_fred_age_uses_eastern_date():
    """03:30 UTC on 09-11 is 23:30 ET on 09-10: a daily series ending 09-04
    is 6 days old (fresh), not 7 (stale by the UTC date)."""
    late = datetime(2026, 9, 11, 3, 30, tzinfo=timezone.utc)
    client = FakeFredClient(bodies={"DGS10": _obs(("2026-09-04", "4.0"))})
    view = await fred.get_fred_view(FakeRedis(), MemoryCooldowns(), client, now=lambda: late)
    assert view["DGS10"]["ageDays"] == 6
    assert view["DGS10"]["stale"] is False
