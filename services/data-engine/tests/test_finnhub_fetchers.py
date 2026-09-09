"""
TradingFirm — Finnhub client + context fetcher tests (Part 2.1).

Zero network: every HTTP call is intercepted by respx with
`assert_all_mocked=True`, so a stray real request fails the test. Bodies
come from the recorded fixtures in tests/fixtures/finnhub/ (written once
by tests/record_finnhub_live.py). Redis is the in-process FakeRedis; the
DB pool is the same mocked asyncpg pool the store tests use. The rate
limiter is tested with a fake clock — no real sleeps anywhere.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from cache import finnhub_key
from db import MARKET_TICKER, upsert_events, upsert_news
from providers.context import finnhub
from providers.context.finnhub_client import (
    FINNHUB_BASE_URL,
    FinnhubAuthError,
    FinnhubClient,
    FinnhubError,
    FinnhubNotConfigured,
    FinnhubRateLimited,
    RateLimiter,
)
from tests.fake_redis import FakeRedis

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "finnhub"
# Inside tf-data-engine-dev the repo's migrations are mounted read-only at
# /migrations (docker-compose.yml); on the host they sit under infra/.
_HERE = Path(__file__).resolve()
MIGRATION = next(
    (p for p in (
        Path("/migrations/003_context.sql"),
        _HERE.parents[3] / "infra" / "supabase" / "migrations" / "003_context.sql" if len(_HERE.parents) > 3 else Path("/nonexistent"),
    ) if p.exists()),
    Path("/migrations/003_context.sql"),
)
TODAY = date(2026, 9, 9)


def _fixture(kind: str):
    return json.loads((FIXTURES / f"AAPL_{kind}.json").read_text())


class _FakeClock:
    """Monotonic clock whose sleep() advances time instead of waiting."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(key: str = "test-key", **kw) -> FinnhubClient:
    """Client with a no-wait limiter (fake clock) unless overridden."""
    fake = _FakeClock()
    limiter = kw.pop("limiter", RateLimiter(clock=fake.clock, sleep=fake.sleep))
    return FinnhubClient(key, limiter=limiter, **kw)


def _make_pool():
    conn = AsyncMock()
    conn.executemany = AsyncMock()
    pool = MagicMock()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


@pytest.fixture
def mock_api():
    # assert_all_called=False: some tests register a route that must NOT be reached.
    with respx.mock(base_url=FINNHUB_BASE_URL, assert_all_mocked=True, assert_all_called=False) as api:
        yield api


# ── Client: failure branches ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_not_configured_raises(mock_api):
    client = _client(key="")
    with pytest.raises(FinnhubNotConfigured):
        await client.get("/stock/profile2", symbol="AAPL")
    assert not mock_api.calls  # no HTTP at all
    await client.aclose()


@pytest.mark.asyncio
async def test_client_sends_token_in_header_not_url(mock_api):
    route = mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, json={"name": "x"}))
    client = _client(key="secret-token")
    await client.get("/stock/profile2", symbol="AAPL")
    request = route.calls.last.request
    assert request.headers["X-Finnhub-Token"] == "secret-token"
    assert "secret-token" not in str(request.url)
    assert request.url.params["symbol"] == "AAPL"
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_client_auth_error(mock_api, status):
    mock_api.get("/stock/profile2").mock(return_value=httpx.Response(status, json={"error": "no"}))
    client = _client()
    with pytest.raises(FinnhubAuthError):
        await client.get("/stock/profile2", symbol="AAPL")
    await client.aclose()


@pytest.mark.asyncio
async def test_client_rate_limited_no_retry(mock_api):
    route = mock_api.get("/stock/profile2").mock(return_value=httpx.Response(429, text="slow down"))
    client = _client()
    with pytest.raises(FinnhubRateLimited):
        await client.get("/stock/profile2", symbol="AAPL")
    assert route.call_count == 1  # exactly one attempt
    await client.aclose()


@pytest.mark.asyncio
async def test_client_server_error(mock_api):
    mock_api.get("/stock/profile2").mock(return_value=httpx.Response(503, text="down"))
    client = _client()
    with pytest.raises(FinnhubError) as exc_info:
        await client.get("/stock/profile2", symbol="AAPL")
    assert type(exc_info.value) is FinnhubError
    await client.aclose()


@pytest.mark.asyncio
async def test_client_timeout(mock_api):
    mock_api.get("/stock/profile2").mock(side_effect=httpx.ConnectTimeout("timed out"))
    client = _client()
    with pytest.raises(FinnhubError) as exc_info:
        await client.get("/stock/profile2", symbol="AAPL")
    assert type(exc_info.value) is FinnhubError
    await client.aclose()


@pytest.mark.asyncio
async def test_client_bad_json(mock_api):
    mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    client = _client()
    with pytest.raises(FinnhubError):
        await client.get("/stock/profile2", symbol="AAPL")
    await client.aclose()


# ── Limiter (fake clock, no real sleeps) ─────────────────────────────────


@pytest.mark.asyncio
async def test_limiter_enforces_min_gap():
    fake = _FakeClock()
    limiter = RateLimiter(max_calls=60, window=60.0, min_gap=1.2, clock=fake.clock, sleep=fake.sleep)
    assert await limiter.acquire() == 0.0
    waited = await limiter.acquire()  # same instant → must wait the full gap
    assert waited == pytest.approx(1.2)
    assert fake.slept == [pytest.approx(1.2)]


@pytest.mark.asyncio
async def test_limiter_blocks_61st_call():
    fake = _FakeClock()
    limiter = RateLimiter(max_calls=60, window=60.0, min_gap=0.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(60):
        assert await limiter.acquire() == 0.0  # 60 calls at t=1000, no waiting
    start = fake.now
    waited = await limiter.acquire()  # 61st: must wait until the first call leaves the window
    assert waited == pytest.approx(60.0)
    assert fake.now == pytest.approx(start + 60.0)
    # At t+60 every call from t has left the window, so the 62nd is free.
    assert await limiter.acquire() == 0.0


# ── Fetchers: happy path on recorded fixtures ────────────────────────────


@pytest.mark.asyncio
async def test_profile_parses_fixture(mock_api):
    body = _fixture("profile")
    mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, json=body))
    client = _client()
    out = await finnhub.profile(client, "aapl")
    assert out == body
    assert out["ticker"] == "AAPL" and out["name"]
    await client.aclose()


@pytest.mark.asyncio
async def test_company_news_parses_fixture(mock_api):
    body = _fixture("news")
    route = mock_api.get("/company-news").mock(return_value=httpx.Response(200, json=body))
    client = _client()
    out = await finnhub.company_news(client, "AAPL", days=7, today=TODAY)
    assert out == body and len(out) > 0
    params = route.calls.last.request.url.params
    assert params["symbol"] == "AAPL"
    assert params["from"] == "2026-09-02" and params["to"] == "2026-09-09"
    rows = finnhub.news_records("AAPL", out)
    assert rows and set(rows[0]) == {"ticker", "published_at", "source", "title", "url", "summary"}
    assert rows[0]["published_at"].tzinfo is timezone.utc
    await client.aclose()


@pytest.mark.asyncio
async def test_recommendations_parses_fixture(mock_api):
    body = _fixture("recommendations")
    mock_api.get("/stock/recommendation").mock(return_value=httpx.Response(200, json=body))
    client = _client()
    out = await finnhub.recommendations(client, "AAPL")
    assert out == body and {"buy", "hold", "sell", "period"} <= set(out[0])
    await client.aclose()


@pytest.mark.asyncio
async def test_earnings_calendar_to_events(mock_api):
    body = _fixture("earnings_calendar")
    route = mock_api.get("/calendar/earnings").mock(return_value=httpx.Response(200, json=body))
    client = _client()
    out = await finnhub.earnings_calendar(client, "AAPL", today=TODAY)
    assert out == body["earningsCalendar"] and len(out) > 0
    params = route.calls.last.request.url.params
    # 730 days back from 2026-09-09 (no Feb 29 inside) and 120 days ahead
    assert params["from"] == "2024-09-09" and params["to"] == "2027-01-07"
    events = finnhub.calendar_events("AAPL", out)
    assert len(events) == len(out)
    first = events[0]
    assert first["event_type"] == "earnings"
    assert first["event_at"] == datetime.strptime(out[0]["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    assert set(first["meta"]) == {"calendar"}
    assert first["meta"]["calendar"]["epsEstimate"] == out[0]["epsEstimate"]
    await client.aclose()


@pytest.mark.asyncio
async def test_earnings_surprises_to_events(mock_api):
    body = _fixture("earnings_surprises")
    mock_api.get("/stock/earnings").mock(return_value=httpx.Response(200, json=body))
    client = _client()
    out = await finnhub.earnings_surprises(client, "AAPL")
    assert out == body and len(out) > 0
    events = finnhub.surprise_events("AAPL", out)
    assert len(events) == len(out)
    first = events[0]
    assert first["event_type"] == "earnings_surprise"
    assert first["event_at"] == datetime.strptime(out[0]["period"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    assert set(first["meta"]) == {"surprise"}
    assert first["meta"]["surprise"]["actual"] == out[0]["actual"]
    await client.aclose()


# ── Fetchers: failure branches ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["AAPL1", "", "TOOLONG", "A.B"])
async def test_fetchers_reject_bad_ticker(mock_api, bad):
    client = _client()
    for fn in (finnhub.profile, finnhub.recommendations, finnhub.earnings_surprises):
        with pytest.raises(ValueError):
            await fn(client, bad)
    with pytest.raises(ValueError):
        await finnhub.company_news(client, bad, today=TODAY)
    with pytest.raises(ValueError):
        await finnhub.earnings_calendar(client, bad, today=TODAY)
    assert not mock_api.calls
    await client.aclose()


@pytest.mark.asyncio
async def test_company_news_empty(mock_api):
    mock_api.get("/company-news").mock(return_value=httpx.Response(200, json=[]))
    client = _client()
    assert await finnhub.company_news(client, "AAPL", today=TODAY) == []
    assert finnhub.news_records("AAPL", []) == []
    await client.aclose()


@pytest.mark.asyncio
async def test_profile_empty(mock_api):
    mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, json={}))
    client = _client()
    assert await finnhub.profile(client, "ZZZZ") == {}
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_repeat_hits_cache(mock_api):
    route = mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, json=_fixture("profile")))
    redis = FakeRedis()
    client = _client()
    first = await finnhub.profile(client, "AAPL", redis=redis)
    second = await finnhub.profile(client, "aapl", redis=redis)  # case shares the key
    assert first == second
    assert route.call_count == 1
    assert redis.keys() == [finnhub_key("profile", "AAPL")]
    assert 0 < await redis.ttl(finnhub_key("profile", "AAPL")) <= 86400
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_news_cache_ttl_is_15_min(mock_api):
    mock_api.get("/company-news").mock(return_value=httpx.Response(200, json=_fixture("news")))
    redis = FakeRedis()
    client = _client()
    await finnhub.company_news(client, "AAPL", redis=redis, today=TODAY)
    assert 0 < await redis.ttl(finnhub_key("news", "AAPL")) <= 900
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_redis_down_uncached(mock_api):
    route = mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, json=_fixture("profile")))
    client = _client()
    await finnhub.profile(client, "AAPL", redis=None)
    await finnhub.profile(client, "AAPL", redis=FakeRedis(fail_on={"get"}))
    assert route.call_count == 2  # nothing cached, both fetched
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_redis_set_raises(mock_api):
    route = mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, json=_fixture("profile")))
    redis = FakeRedis(fail_on={"set"})
    client = _client()
    out = await finnhub.profile(client, "AAPL", redis=redis)
    assert out["ticker"] == "AAPL"
    assert redis.keys() == []
    assert route.call_count == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_corrupt_cache_refetches(mock_api):
    route = mock_api.get("/stock/profile2").mock(return_value=httpx.Response(200, json=_fixture("profile")))
    redis = FakeRedis()
    await redis.set(finnhub_key("profile", "AAPL"), "{not json", ex=100)
    client = _client()
    out = await finnhub.profile(client, "AAPL", redis=redis)
    assert out["ticker"] == "AAPL" and route.call_count == 1
    await client.aclose()


# ── sync_context: fetch + store ──────────────────────────────────────────


def _mock_context_api(mock_api):
    mock_api.get("/company-news").mock(return_value=httpx.Response(200, json=_fixture("news")))
    mock_api.get("/calendar/earnings").mock(return_value=httpx.Response(200, json=_fixture("earnings_calendar")))
    mock_api.get("/stock/earnings").mock(return_value=httpx.Response(200, json=_fixture("earnings_surprises")))


@pytest.mark.asyncio
async def test_sync_context_stores_news_and_events(mock_api):
    _mock_context_api(mock_api)
    pool, conn = _make_pool()
    client = _client()
    result = await finnhub.sync_context(client, "AAPL", pool=pool, today=TODAY)
    assert result["stored"] is True
    assert result["newsFetched"] == len(_fixture("news"))
    assert result["newsSent"] > 0 and result["eventsSent"] > 0
    assert conn.executemany.await_count == 2
    news_query = conn.executemany.await_args_list[0].args[0]
    events_query = conn.executemany.await_args_list[1].args[0]
    assert "INSERT INTO data_engine.news_items" in news_query
    assert "INSERT INTO data_engine.events" in events_query
    assert client.calls_made == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_store_skipped_without_pool(mock_api):
    _mock_context_api(mock_api)
    client = _client()
    result = await finnhub.sync_context(client, "AAPL", pool=None, today=TODAY)
    assert result["stored"] is False and result["newsSent"] == 0 and result["eventsSent"] == 0
    assert result["newsFetched"] > 0  # fetched anyway
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_context_stops_on_rate_limit(mock_api):
    news = mock_api.get("/company-news").mock(return_value=httpx.Response(429, text="slow"))
    cal = mock_api.get("/calendar/earnings").mock(return_value=httpx.Response(200, json={"earningsCalendar": []}))
    pool, conn = _make_pool()
    client = _client()
    with pytest.raises(FinnhubRateLimited):
        await finnhub.sync_context(client, "AAPL", pool=pool, today=TODAY)
    assert news.call_count == 1 and cal.call_count == 0
    conn.executemany.assert_not_called()
    await client.aclose()


# ── Store: SQL + dedup semantics ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_news_sql():
    pool, conn = _make_pool()
    ts = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    sent = await upsert_news(pool, [
        {"ticker": "AAPL", "published_at": ts, "source": "Reuters", "title": "T", "url": "https://x/1", "summary": "s"},
    ])
    assert sent == 1
    query, records = conn.executemany.await_args.args
    assert "INSERT INTO data_engine.news_items" in query
    assert "ON CONFLICT (ticker, url) DO NOTHING" in query
    assert records == [("AAPL", ts, "Reuters", "T", "https://x/1", "s")]


@pytest.mark.asyncio
async def test_upsert_news_dedups_on_url():
    """Same (ticker, url) twice in one batch → one row sent; the SQL's
    ON CONFLICT (ticker, url) covers rows already stored."""
    pool, conn = _make_pool()
    ts = datetime(2026, 9, 8, tzinfo=timezone.utc)
    item = {"ticker": "AAPL", "published_at": ts, "source": "a", "title": "T", "url": "https://x/1", "summary": ""}
    sent = await upsert_news(pool, [item, dict(item, title="T again"), dict(item, url="https://x/2")])
    assert sent == 2
    _query, records = conn.executemany.await_args.args
    assert [r[4] for r in records] == ["https://x/1", "https://x/2"]


@pytest.mark.asyncio
async def test_upsert_news_market_news_uses_sentinel():
    pool, conn = _make_pool()
    ts = datetime(2026, 9, 8, tzinfo=timezone.utc)
    await upsert_news(pool, [{"ticker": None, "published_at": ts, "title": "Fed", "url": "https://x/fed"}])
    _query, records = conn.executemany.await_args.args
    assert records[0][0] == MARKET_TICKER == "_MARKET"


@pytest.mark.asyncio
async def test_upsert_news_drops_items_without_url_or_title():
    pool, conn = _make_pool()
    ts = datetime(2026, 9, 8, tzinfo=timezone.utc)
    sent = await upsert_news(pool, [
        {"ticker": "AAPL", "published_at": ts, "title": "", "url": "https://x/1"},
        {"ticker": "AAPL", "published_at": ts, "title": "T", "url": ""},
        {"ticker": "AAPL", "published_at": None, "title": "T", "url": "https://x/3"},
    ])
    assert sent == 0
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_news_empty_is_noop():
    pool, conn = _make_pool()
    assert await upsert_news(pool, []) == 0
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_news_raise_propagates():
    pool, conn = _make_pool()
    conn.executemany = AsyncMock(side_effect=RuntimeError("connection reset"))
    ts = datetime(2026, 9, 8, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError):
        await upsert_news(pool, [{"ticker": "AAPL", "published_at": ts, "title": "T", "url": "https://x/1"}])


@pytest.mark.asyncio
async def test_upsert_events_sql_merges_meta():
    pool, conn = _make_pool()
    at = datetime(2026, 10, 30, tzinfo=timezone.utc)
    sent = await upsert_events(pool, [
        {"ticker": "AAPL", "event_type": "earnings", "event_at": at, "meta": {"calendar": {"epsEstimate": 1.5}}},
    ])
    assert sent == 1
    query, records = conn.executemany.await_args.args
    assert "ON CONFLICT (ticker, event_type, event_at) DO UPDATE" in query
    assert "meta = data_engine.events.meta || EXCLUDED.meta" in query
    assert records == [("AAPL", "earnings", at, json.dumps({"calendar": {"epsEstimate": 1.5}}))]


@pytest.mark.asyncio
async def test_upsert_events_calendar_after_surprise_keeps_actuals():
    """Calendar rows and surprise rows never share a primary key (report
    date vs fiscal period end, different event_type), and each writer owns
    its own nested meta key — so a calendar run after a surprise run
    cannot clobber the surprise's actuals, in either order."""
    cal = finnhub.calendar_events("AAPL", _fixture("earnings_calendar")["earningsCalendar"])
    sur = finnhub.surprise_events("AAPL", _fixture("earnings_surprises"))
    cal_keys = {(e["event_type"], e["event_at"]) for e in cal}
    sur_keys = {(e["event_type"], e["event_at"]) for e in sur}
    assert cal_keys and sur_keys and not (cal_keys & sur_keys)
    assert all(set(e["meta"]) == {"calendar"} for e in cal)
    assert all(set(e["meta"]) == {"surprise"} for e in sur)

    pool, conn = _make_pool()
    sent = await upsert_events(pool, sur + cal)  # surprise first, then calendar
    assert sent == len(sur) + len(cal)
    _query, records = conn.executemany.await_args.args
    metas = [json.loads(r[3]) for r in records]
    assert sum("surprise" in m for m in metas) == len(sur)
    assert sum("calendar" in m for m in metas) == len(cal)


@pytest.mark.asyncio
async def test_upsert_events_same_row_twice_in_batch_last_wins():
    pool, conn = _make_pool()
    at = datetime(2026, 10, 30, tzinfo=timezone.utc)
    sent = await upsert_events(pool, [
        {"ticker": "AAPL", "event_type": "earnings", "event_at": at, "meta": {"calendar": {"epsEstimate": 1.0}}},
        {"ticker": "AAPL", "event_type": "earnings", "event_at": at, "meta": {"calendar": {"epsEstimate": 2.0}}},
    ])
    assert sent == 1
    _query, records = conn.executemany.await_args.args
    assert json.loads(records[0][3]) == {"calendar": {"epsEstimate": 2.0}}


@pytest.mark.asyncio
async def test_upsert_events_empty_is_noop():
    pool, conn = _make_pool()
    assert await upsert_events(pool, []) == 0
    conn.executemany.assert_not_called()


def test_migration_003_news_ticker_not_null_unique():
    """Guard for the silent-dedup trap: a nullable column inside a UNIQUE
    constraint dedups nothing. ticker must stay NOT NULL."""
    sql = MIGRATION.read_text()
    assert "ticker          VARCHAR(10) NOT NULL" in sql
    assert "UNIQUE (ticker, url)" in sql
    assert "PRIMARY KEY (ticker, event_type, event_at)" in sql
    assert "IF NOT EXISTS" in sql and "INSERT INTO" not in sql
