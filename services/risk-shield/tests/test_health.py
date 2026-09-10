"""Part 3.1 — /health and / response shape, over stubbed app.state (no
lifespan). Correct here because these assert serialization, not startup;
the startup branches live in test_lifespan.py."""

import importlib

import pytest
from fastapi.testclient import TestClient

import config
import main
import news_poller


@pytest.fixture
def stub_state():
    """TestClient without triggering the lifespan: set state by hand."""
    def _make(*, db=object(), redis=object()):
        main.app.state.db_pool = db
        main.app.state.redis = redis
        return TestClient(main.app)
    yield _make
    main.app.state.db_pool = None
    main.app.state.redis = None


def test_health_ok_both_up(stub_state):
    body = stub_state().get("/health").json()
    assert body["service"] == config.settings.service_name
    assert body["status"] == "ok"
    assert body["version"] == "0.2.0"
    assert body["db_connected"] is True
    assert body["redis_connected"] is True
    assert body["fredConfigured"] is False
    assert body["timestamp"].endswith("+00:00")


def test_health_fred_unconfigured(stub_state, monkeypatch):
    monkeypatch.setattr(main.settings, "fred_api_key", config.SecretStr(""))
    assert stub_state().get("/health").json()["fredConfigured"] is False


def test_health_fred_configured_never_leaks_key(stub_state, monkeypatch, caplog):
    """G14: the boolean is the only thing the key produces."""
    secret = "FRED-KEY-9b1d4f7a"
    monkeypatch.setattr(main.settings, "fred_api_key", config.SecretStr(secret))
    client = stub_state()
    with caplog.at_level("DEBUG"):
        health = client.get("/health")
        root = client.get("/")
    assert health.json()["fredConfigured"] is True
    assert secret not in health.text
    assert secret not in root.text
    joined = "\n".join(f"{r.getMessage()} {r.args}" for r in caplog.records)
    assert secret not in joined


def test_root_lists_endpoints(stub_state):
    body = stub_state().get("/").json()
    assert body["service"] == config.settings.service_name
    assert body["docs"] == "/docs"
    assert "GET  /health" in body["endpoints"]
    for route in ("GET  /market/health", "GET  /market/indicators", "GET  /market/history?days=30"):
        assert route in body["endpoints"]


def test_health_reports_scheduter_state(stub_state, monkeypatch):
    """Part 3.4: whether this process schedules checks, and when it last ran one."""
    monkeypatch.setattr(main.settings, "scheduler_enabled", False)
    monkeypatch.setattr(main.app.state, "check_status",
                        {"lastCheckAt": None, "lastKind": None, "lastScore": None, "lastError": None},
                        raising=False)
    body = stub_state().get("/health").json()
    assert body["schedulerEnabled"] is False
    assert body["lastCheckAt"] is None

    monkeypatch.setattr(main.settings, "scheduler_enabled", True)
    main.app.state.check_status["lastCheckAt"] = "2026-09-10T20:20:00+00:00"
    body = stub_state().get("/health").json()
    assert body["schedulerEnabled"] is True
    assert body["lastCheckAt"] == "2026-09-10T20:20:00+00:00"


NEWS_FIELDS = ("newsPollEnabled", "lastNewsPollAt", "newsPageSpanMinutes", "newsOldestAt",
               "newsLastError", "finnhubConfigured")


def test_health_reports_news_poll_state(stub_state, monkeypatch):
    """Part 3.5: the news poller's state. lastNewsPollAt is the last success,
    and the page's span / oldest item show a shrinking page before the overlap
    warning fires. The Finnhub key only ever produces a boolean (G14)."""
    monkeypatch.setattr(main.settings, "news_poll_enabled", False)
    monkeypatch.setattr(main.settings, "finnhub_api_key", config.SecretStr(""))
    monkeypatch.setattr(main.app.state, "news_status", news_poller.initial_news_status(), raising=False)
    body = stub_state().get("/health").json()
    assert {k: body[k] for k in NEWS_FIELDS} == dict.fromkeys(NEWS_FIELDS[:-1]) | {
        "newsPollEnabled": False, "finnhubConfigured": False}

    secret = "FINNHUB-KEY-5d2e9b10"
    monkeypatch.setattr(main.settings, "news_poll_enabled", True)
    monkeypatch.setattr(main.settings, "finnhub_api_key", config.SecretStr(secret))
    main.app.state.news_status.update(
        lastPollAt="2026-09-10T17:45:00+00:00", lastSuccessAt="2026-09-10T17:30:00+00:00",
        pageSpanMinutes=2466, oldestAt="2026-09-09T00:06:48+00:00", lastError="ingest: HTTP 503")
    resp = stub_state().get("/health")
    assert {k: resp.json()[k] for k in NEWS_FIELDS} == {
        "newsPollEnabled": True, "lastNewsPollAt": "2026-09-10T17:30:00+00:00",
        "newsPageSpanMinutes": 2466, "newsOldestAt": "2026-09-09T00:06:48+00:00",
        "newsLastError": "ingest: HTTP 503", "finnhubConfigured": True}
    assert secret not in resp.text


def test_health_reports_macro_brief_flag(stub_state, monkeypatch):
    """Part 3.6a: whether this process may generate macro briefs."""
    monkeypatch.setattr(main.settings, "macro_brief_enabled", False)
    assert stub_state().get("/health").json()["macroBriefEnabled"] is False
    monkeypatch.setattr(main.settings, "macro_brief_enabled", True)
    assert stub_state().get("/health").json()["macroBriefEnabled"] is True
