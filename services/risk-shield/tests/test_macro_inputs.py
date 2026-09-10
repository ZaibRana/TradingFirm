"""Part 3.6a — the macro brief inputs (spec decisions 5–7).

Postgres through db.py's real read helpers over a fake pool, data-engine over
an httpx MockTransport, FRED over a fake client, Redis via FakeRedis. No
socket. Frozen times are ET wall clocks on the XNYS calendar."""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg
import pytest

import db
import macro_inputs
import scheduler

ET = ZoneInfo("America/New_York")


def et(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET).astimezone(timezone.utc)


MONITORS = {
    name: {"score": score, "raw": {"series": [1, 2, 3]}, "detail": f"{name} detail", "stale": False,
           "weight": weight}
    for name, score, weight in (("vix", 60, 25), ("breadth", 60, 20), ("spy_trend", 70, 20),
                                ("sector_rotation", 65, 15), ("volume", 85, 10), ("cross_asset", 80, 10))
}


def _row(at, *, score=68, regime="CAUTIOUS", trend="stable", kind="market", stale=False, monitors=MONITORS):
    return {"checked_at": at, "score": score, "regime": regime, "trend": trend,
            "indicators": json.dumps({"kind": kind, "coverage": 100, "stale": stale,
                                      "staleMonitors": ["vix"] if stale else [], "monitors": monitors,
                                      "inputs": {}, "settleScore": 63, "settleCheckedAt": None})}


class HealthPool:
    """Answers db.py's three health reads by their literal SQL; any write or
    any other statement fails the test."""

    def __init__(self, *, latest=None, scored=None, settle=None, fail=None):
        self.latest, self.scored, self.settle, self.fail = latest, scored, settle, fail
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if self.fail:
            raise self.fail
        if sql == db.LATEST_HEALTH_CHECK_SQL:
            return self.latest
        if sql == db.LATEST_SCORED_HEALTH_CHECK_SQL:
            return self.scored
        if sql == db.SETTLE_BASE_SQL:
            return self.settle
        raise AssertionError(f"unexpected read: {sql}")

    async def fetch(self, *args):
        raise AssertionError("health inputs use fetchrow only")

    async def execute(self, *args):
        raise AssertionError("the inputs never write")


# ── scheduler.last_slot_before ───────────────────────────────────

@pytest.mark.parametrize("now, expected", [
    (et(2026, 9, 10, 14, 7), ("market", et(2026, 9, 10, 14, 5))),     # mid-session
    (et(2026, 9, 10, 14, 5), ("market", et(2026, 9, 10, 14, 5))),     # exactly on a slot
    (et(2026, 9, 10, 16, 21), ("settle", et(2026, 9, 10, 16, 20))),   # just after settle
    (et(2026, 9, 10, 16, 26), ("settle", et(2026, 9, 10, 16, 20))),
    (et(2026, 9, 10, 16, 10), ("market", et(2026, 9, 10, 16, 0))),    # between close and settle
    (et(2026, 9, 11, 7, 25), ("settle", et(2026, 9, 10, 16, 20))),    # pre-market: yesterday's settle
    (et(2026, 9, 12, 10, 0), ("settle", et(2026, 9, 11, 16, 20))),    # Saturday
    (et(2026, 11, 26, 10, 0), ("settle", et(2026, 11, 25, 16, 20))),  # Thanksgiving (XNYS holiday)
    (et(2026, 11, 27, 13, 10), ("market", et(2026, 11, 27, 13, 0))),  # early close, close slot inclusive
    (et(2026, 11, 27, 15, 0), ("market", et(2026, 11, 27, 13, 0))),
    (et(2026, 11, 27, 16, 25), ("settle", et(2026, 11, 27, 16, 20))),
], ids=["mid-session", "on-slot", "16:21", "16:26", "16:10", "pre-market", "saturday",
        "holiday", "early-close-13:10", "early-close-15:00", "early-close-settle"])
def test_last_slot_before(now, expected):
    assert scheduler.last_slot_before(now) == expected


# ── Health section ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inputs_health_from_latest_row():
    now = et(2026, 9, 10, 14, 7)
    pool = HealthPool(latest=_row(et(2026, 9, 10, 14, 5, 2)),
                      settle={"checked_at": et(2026, 9, 9, 16, 20, 2), "score": 63})
    health, settle = await macro_inputs.health_section(pool, now)

    assert health == {
        "status": "ok", "checkedAt": et(2026, 9, 10, 14, 5, 2).isoformat(), "kind": "market",
        "score": 68, "regime": "CAUTIOUS", "trend": "stable", "coverage": 100, "stale": False,
        "staleMonitors": [],
        "monitors": {name: {"score": m["score"], "weight": m["weight"], "stale": False,
                            "detail": f"{name} detail"} for name, m in MONITORS.items()},
        "ageMinutes": 1, "lastExpectedSlotAt": et(2026, 9, 10, 14, 0).isoformat(),
    }
    assert "lastScored" not in health
    assert macro_inputs.health_ready(health) is True
    assert macro_inputs.health_freshness(health) == {
        "healthStatus": "ok", "healthStale": False, "healthMonitorsStale": False, "healthAgeMinutes": 1}
    # Only reads, the latest-scored read skipped for a scored row.
    assert [sql for sql, _ in pool.calls] == [db.LATEST_HEALTH_CHECK_SQL, db.SETTLE_BASE_SQL]


@pytest.mark.asyncio
@pytest.mark.parametrize("now, row_at, kind, stale", [
    (et(2026, 9, 10, 14, 7), et(2026, 9, 10, 14, 5, 2), "market", False),   # in session, on time
    (et(2026, 9, 10, 14, 12), et(2026, 9, 10, 14, 0, 2), "market", True),   # one slot late
    (et(2026, 9, 11, 7, 30), et(2026, 9, 10, 16, 20, 2), "settle", False),  # 07:30 with yesterday's settle
    (et(2026, 9, 11, 7, 30), et(2026, 9, 10, 16, 0, 2), "market", True),    # 07:30, the settle is missing
    (et(2026, 9, 12, 10, 0), et(2026, 9, 11, 16, 20, 3), "settle", False),  # Saturday
], ids=["on-time", "one-slot-late", "0730-with-settle", "0730-without-settle", "saturday"])
async def test_inputs_health_stale_against_last_expected_slot(now, row_at, kind, stale):
    health, _ = await macro_inputs.health_section(HealthPool(latest=_row(row_at, kind=kind)), now)
    fresh = macro_inputs.health_freshness(health)
    assert fresh["healthStale"] is stale
    assert health["kind"] == kind
    if now == et(2026, 9, 11, 7, 30) and not stale:
        # Amendment B: the fresh 07:30 verdict never hides the age. Whoever
        # "fixes" the verdict must see that ~670 minutes is still exposed.
        assert fresh["healthAgeMinutes"] > 600
        assert fresh["healthAgeMinutes"] == health["ageMinutes"] == 909
        assert health["lastExpectedSlotAt"] == et(2026, 9, 10, 16, 20).isoformat()


@pytest.mark.asyncio
async def test_inputs_health_monitors_stale_flag():
    now = et(2026, 9, 10, 14, 7)
    health, _ = await macro_inputs.health_section(HealthPool(latest=_row(et(2026, 9, 10, 14, 5), stale=True)), now)
    fresh = macro_inputs.health_freshness(health)
    assert (fresh["healthStale"], fresh["healthMonitorsStale"]) == (False, True)
    # A stored monitors blob of the wrong shape answers null, never raises.
    health, _ = await macro_inputs.health_section(
        HealthPool(latest=_row(et(2026, 9, 10, 14, 5), monitors={"vix": 5})), now)
    assert health["monitors"] is None


@pytest.mark.asyncio
async def test_inputs_health_null_score_uses_last_scored():
    now = et(2026, 9, 10, 14, 7)
    scored_at = et(2026, 9, 10, 13, 55, 1)
    pool = HealthPool(latest=_row(et(2026, 9, 10, 14, 5), score=None, regime=None, trend=None),
                      scored={"checked_at": scored_at, "score": 66, "regime": "CAUTIOUS"})
    health, _ = await macro_inputs.health_section(pool, now)
    assert health["score"] is None
    assert health["lastScored"] == {"score": 66, "regime": "CAUTIOUS", "checkedAt": scored_at.isoformat()}
    assert macro_inputs.health_ready(health) is True

    none_scored = HealthPool(latest=_row(et(2026, 9, 10, 14, 5), score=None, regime=None, trend=None))
    health, _ = await macro_inputs.health_section(none_scored, now)
    assert health["lastScored"] is None
    assert macro_inputs.health_ready(health) is False


@pytest.mark.asyncio
async def test_inputs_health_no_checks():
    now = et(2026, 9, 10, 14, 7)
    health, settle = await macro_inputs.health_section(HealthPool(), now)
    assert health == {"status": "no_checks", "lastExpectedSlotAt": et(2026, 9, 10, 14, 0).isoformat()}
    assert settle == {"present": False, "checkedAt": None, "score": None}
    assert macro_inputs.health_ready(health) is False
    assert macro_inputs.health_freshness(health) == {
        "healthStatus": "no_checks", "healthStale": True, "healthMonitorsStale": False, "healthAgeMinutes": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("pool", [
    None,
    HealthPool(fail=asyncpg.PostgresError("boom")),
    HealthPool(fail=asyncpg.InterfaceError("closed")),
    HealthPool(fail=ConnectionError("reset")),
    HealthPool(fail=TimeoutError()),
], ids=["no-pool", "postgres-error", "interface-error", "connection-error", "timeout"])
async def test_inputs_health_db_unavailable(pool, caplog):
    """Never raises: the other sections are still built (the assembly and the
    all-dependencies-down endpoint rows prove that half)."""
    now = et(2026, 9, 10, 14, 7)
    with caplog.at_level(logging.WARNING, logger="macro_inputs"):
        health, settle = await macro_inputs.health_section(pool, now)
    assert health["status"] == "unavailable"
    assert settle["present"] is False
    assert macro_inputs.health_ready(health) is False
    assert macro_inputs.health_freshness(health)["healthStale"] is True
    if pool is not None:
        assert any("health read failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_inputs_settle_present_or_absent():
    now = et(2026, 9, 11, 7, 30)
    settle_at = et(2026, 9, 10, 16, 20, 2)
    pool = HealthPool(latest=_row(settle_at, kind="settle", score=63),
                      settle={"checked_at": settle_at, "score": 63})
    _, settle = await macro_inputs.health_section(pool, now)
    assert settle == {"present": True, "checkedAt": settle_at.isoformat(), "score": 63}
    # The settle cutoff is now itself: the latest scored settle before this call.
    (sql, args), = [c for c in pool.calls if c[0] == db.SETTLE_BASE_SQL]
    assert args == (now,)

    _, settle = await macro_inputs.health_section(HealthPool(latest=_row(et(2026, 9, 10, 16, 0))), now)
    assert settle == {"present": False, "checkedAt": None, "score": None}


# ── News section (commit 4b) ─────────────────────────────────────

import asyncio
from types import SimpleNamespace

import httpx

import news_poller

DE_URL = "http://data-engine-dev:8001"


@pytest.fixture(autouse=True)
def _news_env(monkeypatch):
    monkeypatch.setattr(macro_inputs.settings, "data_engine_url", DE_URL)
    macro_inputs._bad_body_logged.clear()


def _item(i, **over):
    body = {"publishedAt": f"2026-09-10T17:{i:02d}:00+00:00", "source": "CNBC", "title": f"Headline {i}",
            "summary": f"Summary {i}", "url": f"https://example.com/{i}"}
    body.update(over)
    return body


def _http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _answer(status=200, json_body=None, content=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=json_body)
    return handler


@pytest.mark.asyncio
async def test_inputs_news_from_data_engine():
    body = [_item(30), _item(20, title="T" * 400, source=None, summary=None), _item(10, summary="S" * 500)]
    async with _http(_answer(json_body=body)) as http:
        news = await macro_inputs.news_section(http)
    assert news == {
        "status": "ok", "cause": None, "hours": 24, "limit": 50, "count": 3, "truncated": 2,
        "trimmedForSize": 0,
        "items": [
            {"publishedAt": "2026-09-10T17:30:00+00:00", "source": "CNBC", "title": "Headline 30",
             "summary": "Summary 30"},
            {"publishedAt": "2026-09-10T17:20:00+00:00", "source": None, "title": "T" * 300, "summary": ""},
            {"publishedAt": "2026-09-10T17:10:00+00:00", "source": "CNBC", "title": "Headline 10",
             "summary": "S" * 300},
        ],
    }
    assert all("url" not in item for item in news["items"])


@pytest.mark.asyncio
async def test_inputs_news_request_within_route_bounds():
    seen = []
    async with _http(_answer(json_body=[_item(1)], seen=seen)) as http:
        await macro_inputs.news_section(http)
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == f"{DE_URL}/news/market"
    assert dict(request.url.params) == {"hours": "24", "limit": "50"}
    # The pinned copy of data-engine's bounds (spec 3.6a decision 2).
    assert (macro_inputs.DATA_ENGINE_NEWS_MAX_HOURS, macro_inputs.DATA_ENGINE_NEWS_MAX_LIMIT) == (168, 100), (
        "data-engine's GET /news/market bounds are pinned there by "
        "test_news_market_bounds_pinned_for_risk_shield. Change both.")
    assert 1 <= macro_inputs.NEWS_HOURS <= macro_inputs.DATA_ENGINE_NEWS_MAX_HOURS
    assert 1 <= macro_inputs.NEWS_LIMIT <= macro_inputs.DATA_ENGINE_NEWS_MAX_LIMIT


@pytest.mark.asyncio
async def test_inputs_news_empty_is_flagged():
    """An empty 24 h is a failure of the feed, not a quiet day: the section
    says so, and the anyStale truth table (commit 4c) counts it."""
    async with _http(_answer(json_body=[])) as http:
        news = await macro_inputs.news_section(http)
    assert (news["status"], news["count"], news["items"], news["cause"]) == ("empty", 0, [], None)
    state = SimpleNamespace(news_status=news_poller.initial_news_status())
    assert macro_inputs.news_freshness(news, state, et(2026, 9, 10, 14, 7))["newsStatus"] == "empty"


def _raise(exc):
    def handler(request):
        raise exc
    return handler


async def _slow(request):
    await asyncio.sleep(1)
    return httpx.Response(200, json=[])


@pytest.mark.asyncio
@pytest.mark.parametrize("handler, cause", [
    (_raise(httpx.ConnectError("refused")), "ConnectError"),
    (_raise(httpx.ReadTimeout("slow")), "timeout"),
    (_slow, "timeout"),
    (_answer(307), "HTTP 307"),
    (_answer(404, json_body={"detail": "Not Found"}), "HTTP 404"),
    (_answer(422, json_body={"detail": []}), "HTTP 422"),
    (_answer(500, content=b"boom"), "HTTP 500"),
    (_answer(503, json_body={"detail": "database unavailable"}), "HTTP 503"),
], ids=["transport", "httpx-timeout", "hard-timeout", "307", "404", "422", "500", "503"])
async def test_inputs_news_unavailable(handler, cause, monkeypatch, caplog):
    monkeypatch.setattr(macro_inputs, "NEWS_TIMEOUT", 0.05)
    calls = []

    async def counting(request):
        calls.append(request)
        result = handler(request)
        return await result if asyncio.iscoroutine(result) else result

    with caplog.at_level(logging.WARNING, logger="macro_inputs"):
        async with _http(counting) as http:
            news = await macro_inputs.news_section(http)
    assert (news["status"], news["cause"], news["items"]) == ("unavailable", cause, [])
    assert len(calls) == 1                     # no retry, no redirect followed
    assert [r.levelname for r in caplog.records] == ["WARNING"]


@pytest.mark.asyncio
async def test_inputs_news_bad_body(caplog):
    bodies = [
        ({"items": []}, "not a list"),
        ([_item(1), {k: v for k, v in _item(2).items() if k != "title"}], "item missing title"),
    ]
    with caplog.at_level(logging.DEBUG, logger="macro_inputs"):
        for body, _ in bodies + bodies:          # each problem twice
            async with _http(_answer(json_body=body)) as http:
                news = await macro_inputs.news_section(http)
            assert (news["status"], news["cause"], news["items"]) == ("unavailable", "bad body", [])
        async with _http(_answer(content=b"<html>")) as http:
            assert (await macro_inputs.news_section(http))["cause"] == "bad body"
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 3                      # one per distinct problem, never repeated
    assert any("not a list" in m for m in errors) and any("item missing title" in m for m in errors)
    assert any("not JSON" in m for m in errors)


def test_inputs_freshness_carries_news_poll_state(monkeypatch):
    now = et(2026, 9, 10, 14, 7)
    news = {"status": "ok"}
    status = news_poller.initial_news_status()
    status.update(startedAt=(now - timedelta(hours=5)).isoformat(),
                  lastSuccessAt=(now - timedelta(minutes=61)).isoformat(), lastError="ingest: HTTP 503")
    state = SimpleNamespace(news_status=status)

    monkeypatch.setattr(news_poller.settings, "news_poll_enabled", True)
    fresh = macro_inputs.news_freshness(news, state, now)
    assert fresh == {"newsStatus": "ok", "newsPollStale": True, "lastNewsPollAt": status["lastSuccessAt"],
                     "newsLastError": "ingest: HTTP 503"}
    assert {k: fresh[k] for k in news_poller.stale_view(state, now)} == news_poller.stale_view(state, now)

    status["lastSuccessAt"] = (now - timedelta(minutes=60)).isoformat()
    assert macro_inputs.news_freshness(news, state, now)["newsPollStale"] is False

    monkeypatch.setattr(news_poller.settings, "news_poll_enabled", False)
    assert macro_inputs.news_freshness(news, state, now)["newsPollStale"] is None
