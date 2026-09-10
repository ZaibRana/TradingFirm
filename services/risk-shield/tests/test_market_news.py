"""Part 3.5 — the market news poller, on the 2026-09-10 Finnhub fixture.
Converter rows (commit 4a), poll rows (4b). No network: Finnhub and
data-engine are in-process fakes, Redis is tests/fake_redis.py. The twin
guard is here too, green before any poller code runs inside the twin."""

import asyncio
import copy
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import cache
import config
import news_poller
from cache import MemoryCooldowns
from monitors.errors import FinnhubError, FinnhubNotAuthorized, FinnhubRateLimited
from news_poller import market_news_items
from tests.fake_redis import FakeRedis

FIXTURE = Path(__file__).parent / "fixtures" / "finnhub" / "general_news.json"
ITEM_KEYS = {"publishedAt", "title", "url", "source", "summary"}


def _page():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _raw(**over):
    item = {"category": "business", "datetime": 1757524349, "headline": "Headline", "id": 1,
            "image": "", "related": "", "source": "Reuters", "summary": "Summary.",
            "url": "https://example.com/a"}
    item.update(over)
    return item


def _fake_ingest_status(items):
    """data-engine's /news/ingest validation, restated from spec 3.5 decision 5
    with the pinned numbers: 200 when the batch would pass, else 422."""
    if not 1 <= len(items) <= 200:
        return 422
    for item in items:
        if set(item) - ITEM_KEYS or not {"publishedAt", "title", "url"} <= set(item):
            return 422
        if any("\x00" in str(v) for v in item.values()):
            return 422
        at = datetime.fromisoformat(item["publishedAt"])
        if at.tzinfo is None:
            return 422
        if not item["url"].startswith(("http://", "https://")) or len(item["url"]) > 2048:
            return 422
        if not item["title"].strip() or len(item["title"]) > 1000:
            return 422
        if len(item.get("summary", "")) > 10000 or len(item.get("source", "")) > 100:
            return 422
    return 200


def test_market_news_items_from_fixture_and_drops():
    page = _page()
    assert len(page) == 20
    items, counts = market_news_items(page)
    assert counts == {"fetched": 20, "kept": 20, "dropped": 0, "truncated": 0}
    assert all(set(item) == ITEM_KEYS for item in items)
    stamps = [datetime.fromisoformat(item["publishedAt"]) for item in items]
    assert stamps == sorted(stamps)                                   # oldest first
    assert all(s.utcoffset().total_seconds() == 0 for s in stamps)    # aware UTC
    newest = max(page, key=lambda it: it["datetime"])
    assert items[-1]["url"] == newest["url"] and items[-1]["title"] == newest["headline"]

    bad = [
        "not an object",
        {k: v for k, v in _raw().items() if k != "url"},
        {k: v for k, v in _raw().items() if k != "headline"},
        {k: v for k, v in _raw().items() if k != "datetime"},
        _raw(url="ftp://example.com/a"),
        _raw(url="   "),
        _raw(headline="   "),
        _raw(datetime=0),
        _raw(datetime=-5),
        _raw(datetime=True),
        _raw(datetime="1757524349"),
        _raw(datetime=1757524349.5),
        _raw(datetime=10**20),                                        # out of range
    ]
    items, counts = market_news_items(page + bad)
    assert counts == {"fetched": 33, "kept": 20, "dropped": 13, "truncated": 0}

    assert market_news_items([]) == ([], {"fetched": 0, "kept": 0, "dropped": 0, "truncated": 0})
    assert market_news_items({"error": "not a list"})[1]["fetched"] == 0


def test_converter_limits_pinned_to_spec():
    # Spec 3.5 decision 5. data-engine's route holds the same numbers
    # (test_ingest_limits_pinned_to_spec); change both or neither.
    assert (news_poller.NEWS_INGEST_MAX_ITEMS, news_poller.NEWS_URL_MAX, news_poller.NEWS_TITLE_MAX,
            news_poller.NEWS_SUMMARY_MAX, news_poller.NEWS_SOURCE_MAX) == (200, 2048, 1000, 10000, 100)


def test_oversize_item_truncated_or_dropped_before_send():
    raw = [
        _raw(url="https://example.com/" + "u" * 2500, headline="long url"),
        _raw(url="https://example.com/\x00long", datetime=1757524350,
             headline="\x00" + "T" * 1500, summary="S\x00" * 10000, source="R\x00" * 150),
        _raw(url="https://example.com/nul-only", datetime=1757524351, headline="\x00 \x00"),
    ]
    # The fake ingest really rejects the untreated item: the check has teeth.
    untreated = {"publishedAt": "2026-09-10T17:12:30+00:00", "title": raw[1]["headline"],
                 "url": raw[1]["url"], "source": raw[1]["source"], "summary": raw[1]["summary"]}
    assert _fake_ingest_status([untreated]) == 422

    items, counts = market_news_items(raw)
    assert counts == {"fetched": 3, "kept": 1, "dropped": 2, "truncated": 1}
    (item,) = items
    assert item["url"] == "https://example.com/long"
    assert item["title"] == "T" * 1000
    assert item["summary"] == "S" * 10000
    assert item["source"] == "R" * 100
    assert not any("\x00" in v for v in item.values())
    assert _fake_ingest_status(items) == 200

    # The whole recorded page passes too, chunked at the pinned size.
    fixture_items, _ = market_news_items(_page())
    assert _fake_ingest_status(fixture_items[:news_poller.NEWS_INGEST_MAX_ITEMS]) == 200


def test_twin_never_ingests_into_prod_data_engine():
    """The dev twin must never reach prod's data-engine (and so prod's
    database), never poll, and hold no Finnhub key. Guards the compose
    overrides DATA_ENGINE_URL / NEWS_POLL_ENABLED / FINNHUB_API_KEY: fails if
    one is dropped. Runs only inside the twin (SERVICE_NAME=risk-shield-dev)."""
    if os.environ.get("SERVICE_NAME") != "risk-shield-dev":
        pytest.skip("not the risk-shield dev twin")
    live = config.Settings()
    assert live.data_engine_url == "http://data-engine-dev:8001"
    assert "//data-engine:" not in live.data_engine_url
    assert live.news_poll_enabled is False
    assert live.finnhub_configured is False


# ── poll_once (commit 4b) ────────────────────────────────────────

T0 = datetime(2026, 9, 10, 17, 30, tzinfo=timezone.utc)     # after the fixture's newest item
LATER = T0 + timedelta(minutes=15)
DE_URL = "http://data-engine-dev:8001"
INGEST_URL = f"{DE_URL}/news/ingest"
OWN_COOLDOWN = cache.cooldown_key(cache.SOURCE_FINNHUB)


@pytest.fixture(autouse=True)
def _poller_env(monkeypatch, caplog):
    monkeypatch.setattr(news_poller.settings, "data_engine_url", DE_URL)
    caplog.set_level(logging.INFO, logger="news_poller")


class FakeFinnhub:
    def __init__(self, page=None, *, error=None, configured=True, events=None):
        self.page = _page() if page is None else page
        self.error, self.configured, self.events = error, configured, events
        self.calls = 0

    async def general_news(self):
        self.calls += 1
        if self.events is not None:
            self.events.append("fetch")
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.page)


class FakeIngest:
    """data-engine's /news/ingest. Answers are used in order and the last one
    repeats: an int is a status, an exception is raised, a float sleeps that
    long first (for the hard timeout)."""

    def __init__(self, *answers, events=None):
        self.answers = list(answers) or [200]
        self.posts = []
        self.events = events

    async def post(self, url, json=None):
        self.posts.append((url, json))
        if self.events is not None:
            self.events.append("ingest")
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, float):
            await asyncio.sleep(answer)
            answer = 200
        return httpx.Response(answer, json={"received": len(json["items"]), "sent": len(json["items"])})


class RecordingRedis(FakeRedis):
    def __init__(self, events, **kw):
        super().__init__(**kw)
        self.events = events

    async def ttl(self, key):
        self.events.append(f"ttl:{key}")
        return await super().ttl(key)


def _state(redis="fake"):
    return SimpleNamespace(redis=FakeRedis() if redis == "fake" else redis,
                           cooldowns=MemoryCooldowns(),
                           news_status=news_poller.initial_news_status())


async def _poll(state, finnhub=None, ingest=None, at=T0):
    finnhub = finnhub or FakeFinnhub()
    ingest = ingest or FakeIngest()
    result = await news_poller.poll_once(state, finnhub, ingest, clock=lambda: at)
    return result, finnhub, ingest


def _bounds(page):
    items, _ = market_news_items(page)
    return datetime.fromisoformat(items[0]["publishedAt"]), datetime.fromisoformat(items[-1]["publishedAt"])


def _logged(caplog, text):
    return [r.levelname for r in caplog.records if text in r.getMessage()]


def _many(n):
    return [_raw(url=f"https://example.com/{i}", headline=f"Item {i}", datetime=1757500000 + 60 * i, id=i)
            for i in range(n)]


@pytest.mark.asyncio
async def test_poll_once_order(monkeypatch):
    events = []
    real = news_poller.market_news_items

    def converting(raw):
        events.append("convert")
        return real(raw)

    monkeypatch.setattr(news_poller, "market_news_items", converting)
    redis = RecordingRedis(events)
    state = _state(redis)
    result, _, _ = await _poll(state, FakeFinnhub(events=events), FakeIngest(events=events))

    assert result == {"outcome": "success", "cause": None}
    assert events == [f"ttl:{OWN_COOLDOWN}", f"ttl:{cache.DATA_ENGINE_FINNHUB_COOLDOWN_KEY}",
                      "fetch", "convert", "ingest"]
    assert redis.get_calls == [] and redis.set_calls == []          # no news state in Redis
    s = state.news_status
    assert s["lastPollAt"] == s["lastSuccessAt"] == T0.isoformat()
    assert (s["fetched"], s["sent"], s["dropped"], s["truncated"], s["lastError"], s["consecutive422"]) == (
        20, 20, 0, 0, None, 0)


@pytest.mark.asyncio
async def test_ingest_payload_shape():
    _, _, ingest = await _poll(_state())
    ((url, body),) = ingest.posts
    assert url == INGEST_URL
    assert set(body) == {"items"}
    items = body["items"]
    assert len(items) == 20
    assert all(set(item) == ITEM_KEYS for item in items)               # no ticker, nothing extra
    stamps = [datetime.fromisoformat(item["publishedAt"]) for item in items]
    assert all(s.tzinfo is not None for s in stamps) and stamps == sorted(stamps)
    json.dumps(body, allow_nan=False)


@pytest.mark.asyncio
async def test_poll_once_empty_page_is_not_a_success(caplog):
    state = _state()
    result, _, ingest = await _poll(state, FakeFinnhub([]))
    assert result == {"outcome": "failed", "cause": "finnhub: empty page"}
    assert ingest.posts == []
    assert state.news_status["lastSuccessAt"] is None
    assert state.news_status["lastError"] == "finnhub: empty page"
    assert _logged(caplog, "empty page") == ["WARNING"]


@pytest.mark.asyncio
async def test_poll_once_all_dropped_is_not_a_success(caplog):
    state = _state()
    page = [_raw(url="ftp://example.com/1"), _raw(headline="   "), _raw(datetime=0)]
    result, _, ingest = await _poll(state, FakeFinnhub(page))
    assert result == {"outcome": "failed", "cause": "convert: every item dropped"}
    assert ingest.posts == []
    assert (state.news_status["fetched"], state.news_status["dropped"]) == (3, 3)
    assert state.news_status["lastSuccessAt"] is None
    assert _logged(caplog, "every one of 3 items was dropped") == ["WARNING"]


@pytest.mark.asyncio
async def test_poll_once_overlap_warning(caplog):
    oldest, newest = _bounds(_page())
    missed = "items may have been missed"
    state = _state()

    await _poll(state)                                          # first poll after start: nothing to compare
    assert _logged(caplog, missed) == []

    for previous, expected in ((newest, []),                   # page reaches back past it
                               (oldest, []),                   # exactly as far back: no gap
                               (oldest - timedelta(seconds=1), ["WARNING"])):
        caplog.clear()
        state.news_status["lastSuccessAt"] = previous.isoformat()
        result, _, ingest = await _poll(state, at=LATER)
        assert _logged(caplog, missed) == expected
        assert result["outcome"] == "success" and len(ingest.posts) == 1   # the ingest still runs


@pytest.mark.asyncio
async def test_news_status_records_page_span_and_oldest():
    page = _page()
    oldest, newest = _bounds(page)
    state = _state()
    await _poll(state)
    full_span = int((newest - oldest).total_seconds() // 60)
    assert state.news_status["pageSpanMinutes"] == full_span
    assert state.news_status["oldestAt"] == oldest.isoformat()

    small = sorted(page, key=lambda it: it["datetime"])[-3:]
    s_oldest, s_newest = _bounds(small)
    await _poll(state, FakeFinnhub(small), at=LATER)
    assert state.news_status["pageSpanMinutes"] == int((s_newest - s_oldest).total_seconds() // 60) < full_span
    assert state.news_status["oldestAt"] == s_oldest.isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [FinnhubError("general news: timeout"),
                                   FinnhubError("general news: HTTP 500")], ids=["timeout", "500"])
async def test_poll_once_finnhub_error_sends_nothing(error, caplog):
    redis = FakeRedis()
    state = _state(redis)
    result, finnhub, ingest = await _poll(state, FakeFinnhub(error=error))
    assert result == {"outcome": "failed", "cause": "finnhub: FinnhubError"}
    assert finnhub.calls == 1 and ingest.posts == []
    assert redis.set_calls == []                                    # not a refusal: no cooldown
    assert state.news_status["lastSuccessAt"] is None
    assert _logged(caplog, str(error)) == ["WARNING"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error, ttl", [
    (FinnhubRateLimited("general news: HTTP 429"), 900),
    (FinnhubNotAuthorized("general news: HTTP 401"), 3600),
    (FinnhubNotAuthorized("general news: HTTP 403"), 3600),
], ids=["429", "401", "403"])
async def test_poll_once_refusal_starts_cooldown_and_skips(error, ttl):
    redis = FakeRedis()
    state = _state(redis)
    result, _, ingest = await _poll(state, FakeFinnhub(error=error))
    assert result == {"outcome": "failed", "cause": f"finnhub: {type(error).__name__}"}
    assert redis.ttls[OWN_COOLDOWN] == ttl and ingest.posts == []

    again = FakeFinnhub()
    result, _, ingest = await _poll(state, again, at=LATER)
    assert result == {"outcome": "skipped", "cause": "skipped: cooldown"}
    assert again.calls == 0 and ingest.posts == []

    # No Redis: the memory clock remembers the refusal (900 s either way).
    state = _state(None)
    await _poll(state, FakeFinnhub(error=error))
    result, again, _ = await _poll(state, FakeFinnhub(), at=LATER)
    assert result["cause"] == "skipped: cooldown" and again.calls == 0


@pytest.mark.asyncio
async def test_poll_once_skips_while_data_engine_finnhub_cooldown(caplog):
    assert cache.DATA_ENGINE_FINNHUB_COOLDOWN_KEY == "tf:cache:finnhub"
    redis = FakeRedis()
    await redis.set("tf:cache:finnhub", "1", ex=42)                 # data-engine's write, not ours
    result, finnhub, ingest = await _poll(_state(redis))
    assert result == {"outcome": "skipped", "cause": "skipped: data-engine cooldown"}
    assert finnhub.calls == 0 and ingest.posts == []
    assert redis.set_calls == [("tf:cache:finnhub", "1", 42)]      # the poller never writes it

    no_expiry = FakeRedis()
    no_expiry.store["tf:cache:finnhub"] = "1"                       # TTL -1: not a running cooldown
    for r in (FakeRedis(), no_expiry, FakeRedis(fail_ttl=True), None):
        result, finnhub, _ = await _poll(_state(r))
        assert result["outcome"] == "success" and finnhub.calls == 1
    assert "treating as clear" in caplog.text


@pytest.mark.asyncio
async def test_poll_once_unconfigured_skips(caplog):
    state = _state()
    result, finnhub, ingest = await _poll(state, FakeFinnhub(configured=False))
    assert result == {"outcome": "skipped", "cause": "skipped: unconfigured"}
    assert finnhub.calls == 0 and ingest.posts == []
    assert _logged(caplog, "FINNHUB_API_KEY is not set") == ["WARNING"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, cause", [
    (httpx.ConnectError("refused"), "ingest: ConnectError"),
    (httpx.ReadTimeout("slow"), "ingest: timeout"),
    (OSError("socket"), "ingest: OSError"),
    (5.0, "ingest: timeout"),                                       # the hard wait_for bound
], ids=["connect", "read-timeout", "oserror", "hard-timeout"])
async def test_poll_once_ingest_failure_is_not_a_success(answer, cause, caplog, monkeypatch):
    monkeypatch.setattr(news_poller, "INGEST_TIMEOUT", 0.05)
    state = _state()
    ingest = FakeIngest(answer, 200)
    result, _, _ = await _poll(state, ingest=ingest)
    assert result == {"outcome": "failed", "cause": cause}
    assert state.news_status["lastSuccessAt"] is None
    assert state.news_status["lastError"] == cause
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    result, _, _ = await _poll(state, ingest=ingest, at=LATER)      # the next poll resends the page
    assert result["outcome"] == "success"
    assert ingest.posts[0] == ingest.posts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [307, 400, 401, 403, 404, 500, 503])
async def test_poll_once_ingest_non_200_warns(code, caplog):
    state = _state()
    result, _, _ = await _poll(state, ingest=FakeIngest(code))
    assert result == {"outcome": "failed", "cause": f"ingest: HTTP {code}"}
    assert _logged(caplog, f"HTTP {code}") == ["WARNING"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert state.news_status["lastSuccessAt"] is None
    assert state.news_status["consecutive422"] == 0


@pytest.mark.asyncio
async def test_poll_once_ingest_422_is_error_not_skipped(caplog):
    state = _state()
    ingest = FakeIngest(422)
    result, _, _ = await _poll(state, ingest=ingest)
    assert result == {"outcome": "failed", "cause": "ingest: HTTP 422"}
    assert _logged(caplog, "(422)") == ["ERROR"]
    assert state.news_status["lastSuccessAt"] is None

    await _poll(state, ingest=ingest, at=LATER)
    assert len(ingest.posts) == 2 and ingest.posts[0] == ingest.posts[1]   # the same page again


@pytest.mark.asyncio
async def test_poll_once_repeated_422_error_then_warning(caplog):
    state = _state()
    ingest = FakeIngest(422, 422, 422, 200, 422)
    seen, counts = [], []
    for n in range(5):
        caplog.clear()
        await _poll(state, ingest=ingest, at=T0 + timedelta(minutes=15 * n))
        seen.append([(r.levelname, r.getMessage()) for r in caplog.records if "(422)" in r.getMessage()])
        counts.append(state.news_status["consecutive422"])
    assert [[level for level, _ in logs] for logs in seen] == [["ERROR"], ["WARNING"], ["WARNING"], [], ["ERROR"]]
    assert "3 slots in a row" in seen[2][0][1]
    assert counts == [1, 2, 3, 0, 1]                                # only a success resets it


@pytest.mark.asyncio
async def test_poll_once_chunks_success_only_after_all():
    state = _state()
    ingest = FakeIngest(200, 500)
    result, _, _ = await _poll(state, FakeFinnhub(_many(250)), ingest)
    assert [len(body["items"]) for _, body in ingest.posts] == [200, 50]
    assert result == {"outcome": "failed", "cause": "ingest: HTTP 500"}
    assert state.news_status["lastSuccessAt"] is None
    assert state.news_status["sent"] == 200

    ingest = FakeIngest(200)
    result, _, _ = await _poll(state, FakeFinnhub(_many(250)), ingest, at=LATER)
    assert result["outcome"] == "success" and state.news_status["sent"] == 250
    assert ingest.posts[0][1]["items"][0]["url"] == "https://example.com/0"    # oldest first


@pytest.mark.asyncio
async def test_poll_once_repeat_page_is_resent_whole():
    redis = FakeRedis()
    state = _state(redis)
    ingest = FakeIngest(200)
    first, _, _ = await _poll(state, ingest=ingest)
    second, _, _ = await _poll(state, ingest=ingest, at=LATER)
    assert first["outcome"] == second["outcome"] == "success"
    assert len(ingest.posts) == 2 and ingest.posts[0] == ingest.posts[1]
    assert len(ingest.posts[0][1]["items"]) == 20
    assert redis.get_calls == [] and redis.set_calls == []          # no state consulted or kept


# ── Staleness (commit 4c-1) ──────────────────────────────────────

def test_news_poll_stale_after_60_minutes(monkeypatch):
    monkeypatch.setattr(news_poller.settings, "news_poll_enabled", True)
    status = news_poller.initial_news_status()
    state = SimpleNamespace(news_status=status)
    view = lambda at: news_poller.stale_view(state, at)

    assert view(T0)["newsPollStale"] is None                               # enabled, not started yet
    status["startedAt"] = T0.isoformat()
    assert view(T0 + timedelta(seconds=3600)) == {"newsPollStale": False, "lastNewsPollAt": None,
                                                  "newsLastError": None}
    assert view(T0 + timedelta(seconds=3601))["newsPollStale"] is True    # no success since start

    success = T0 + timedelta(hours=2)
    status["lastSuccessAt"] = success.isoformat()
    assert view(success + timedelta(seconds=3600))["newsPollStale"] is False
    assert view(success + timedelta(seconds=3601)) == {"newsPollStale": True,
                                                       "lastNewsPollAt": success.isoformat(),
                                                       "newsLastError": None}

    monkeypatch.setattr(news_poller.settings, "news_poll_enabled", False)
    assert view(success + timedelta(hours=5))["newsPollStale"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cause, make_finnhub, make_ingest, cooldown", [
    ("ingest: HTTP 422", FakeFinnhub, lambda: FakeIngest(422), False),
    ("finnhub: FinnhubError", lambda: FakeFinnhub(error=FinnhubError("general news: HTTP 500")), FakeIngest, False),
    ("skipped: cooldown", FakeFinnhub, FakeIngest, True),
    ("ingest: ConnectError", FakeFinnhub, lambda: FakeIngest(httpx.ConnectError("refused")), False),
    ("finnhub: empty page", lambda: FakeFinnhub([]), FakeIngest, False),
], ids=["422", "finnhub-500", "cooldown", "data-engine-down", "empty-page"])
async def test_news_poll_stale_for_any_cause(cause, make_finnhub, make_ingest, cooldown, monkeypatch):
    monkeypatch.setattr(news_poller.settings, "news_poll_enabled", True)
    redis = FakeRedis()
    state = _state(redis)
    state.news_status["startedAt"] = T0.isoformat()
    first, _, _ = await _poll(state, at=T0)
    assert first["outcome"] == "success"
    if cooldown:
        redis.store[OWN_COOLDOWN] = "1"
        redis.ttls[OWN_COOLDOWN] = 900

    for minutes in (15, 30, 45, 61):
        await _poll(state, make_finnhub(), make_ingest(), at=T0 + timedelta(minutes=minutes))

    assert news_poller.stale_view(state, T0 + timedelta(minutes=60))["newsPollStale"] is False
    assert news_poller.stale_view(state, T0 + timedelta(minutes=61)) == {
        "newsPollStale": True, "lastNewsPollAt": T0.isoformat(), "newsLastError": cause}
