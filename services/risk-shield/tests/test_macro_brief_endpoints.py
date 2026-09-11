"""Part 3.6b — GET /macro/brief and /health's brief fields (spec decision 6).
Postgres through db.py's real helpers over a fake pool; app.state is stubbed
without the lifespan, as in test_health.py. No socket."""

import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest
from fastapi.testclient import TestClient

import db
import main
from tests.fake_ai_agent import brief

NOW = datetime(2026, 9, 11, 20, 45, tzinfo=timezone.utc)
GENERATED_AT = datetime(2026, 9, 11, 20, 31, 10, tzinfo=timezone.utc)      # the 16:30 ET slot's brief
BRIEF_ID = "3f0c9a52-6d1e-4b7a-9c1f-2a6e8d4b5c70"
INPUTS = {"schemaVersion": 1, "ready": True, "news": {"status": "ok", "hours": 25, "items": []},
          "freshness": {"healthStale": False, "newsStatus": "ok", "anyStale": False}}
BODY = {"id": BRIEF_ID, "generatedAt": GENERATED_AT.isoformat(), "ageMinutes": 13, "trigger": "slot",
        "regime": "CAUTIOUS", "healthScore": 64, "briefText": brief()["oneParagraph"], "brief": brief(),
        "freshness": INPUTS["freshness"]}


def stored(**over):
    """A risk.macro_briefs row as asyncpg returns it: JSONB as text, the id a UUID."""
    row = {"id": uuid.UUID(BRIEF_ID), "generated_at": GENERATED_AT, "trigger": "slot", "regime": "CAUTIOUS",
           "health_score": 64, "brief_text": brief()["oneParagraph"], "brief": json.dumps(brief()),
           "inputs": json.dumps(INPUTS)}
    return {**row, **over}


class BriefPool:
    """Answers db.py's macro brief reads by their literal SQL and records
    them; `fail` raises on every call. A write fails the test."""

    def __init__(self, *, latest=None, fail=None):
        self.latest, self.fail, self.calls = latest, fail, []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if self.fail:
            raise self.fail
        if sql == db.LATEST_MACRO_BRIEF_SQL:
            return self.latest
        raise AssertionError(f"unexpected read: {sql}")

    async def execute(self, *args):
        raise AssertionError("GET /macro/brief never writes")


@pytest.fixture
def client_with(monkeypatch):
    monkeypatch.setattr(main, "_now", lambda: NOW)

    def _make(pool=None, *, brief_status=None):
        main.app.state.db_pool, main.app.state.redis = pool, None
        monkeypatch.setattr(main.app.state, "brief_status", brief_status, raising=False)
        return TestClient(main.app)
    yield _make
    main.app.state.db_pool = None


def test_get_macro_brief_latest(client_with, monkeypatch):
    pool = BriefPool(latest=stored())
    client = client_with(pool)
    for flag in (False, True):                                   # whatever MACRO_BRIEF_ENABLED says
        monkeypatch.setattr(main.settings, "macro_brief_enabled", flag)
        for path in ("/macro/brief", "/macro/brief?includeInputs=false"):
            resp = client.get(path)
            assert (resp.status_code, resp.json()) == (200, BODY), path
    with_inputs = client.get("/macro/brief?includeInputs=true").json()
    assert with_inputs == {**BODY, "inputs": INPUTS} and list(with_inputs)[-1] == "inputs"
    assert [sql for sql, _ in pool.calls] == [db.LATEST_MACRO_BRIEF_SQL] * 5       # one read a request, nothing else
    assert "GET  /macro/brief" in client.get("/").json()["endpoints"]

    # A JSONB codec hands back objects already; stored inputs that are not an object give freshness null, never 500.
    assert client_with(BriefPool(latest=stored(brief=brief(), inputs=INPUTS))).get("/macro/brief").json() == BODY
    odd = client_with(BriefPool(latest=stored(inputs="[1]"))).get("/macro/brief?includeInputs=true").json()
    assert (odd["freshness"], odd["inputs"]) == (None, [1])


def test_get_macro_brief_404_before_first(client_with):
    client = client_with(BriefPool(latest=None))
    for path in ("/macro/brief", "/macro/brief?includeInputs=true"):
        resp = client.get(path)
        assert (resp.status_code, resp.json()) == (404, {"detail": "no macro brief yet"}), path
    assert client.get("/macro/briefs").json() == {"detail": "Not Found"}         # distinguishable from a wrong route


@pytest.mark.parametrize("pool", [
    None,
    BriefPool(fail=asyncpg.InterfaceError("connection lost")),
    BriefPool(fail=asyncpg.PostgresError("boom")),
    BriefPool(fail=ConnectionError("reset")),
    BriefPool(fail=TimeoutError("command timeout")),
], ids=["no-pool", "interface-error", "postgres-error", "connection-error", "timeout"])
def test_get_macro_brief_db_unavailable_503(client_with, pool, caplog):
    with caplog.at_level(logging.WARNING, logger="risk-shield"):
        resp = client_with(pool).get("/macro/brief")
    assert (resp.status_code, resp.json()) == (503, {"detail": "database unavailable"})
    assert any("latest_macro_brief" in r.getMessage() for r in caplog.records) is (pool is not None)


HEALTH_FIELDS = ("lastBriefAt", "lastBriefTrigger", "lastBriefError")


@pytest.mark.parametrize("brief_status, expected", [
    (None, (None, None, None)),
    ({"lastAttemptAt": "2026-09-11T20:30:00+00:00", "lastBriefAt": "2026-09-11T20:31:10+00:00",
      "lastBriefId": BRIEF_ID, "lastTrigger": "slot", "lastError": None},
     ("2026-09-11T20:31:10+00:00", "slot", None)),
    ({"lastAttemptAt": "2026-09-11T11:30:00+00:00", "lastBriefAt": None, "lastBriefId": None,
      "lastTrigger": None, "lastError": "inputs not ready"},
     (None, None, "inputs not ready")),
    ({"lastAttemptAt": "2026-09-14T11:30:00+00:00", "lastBriefAt": "2026-09-11T20:31:10+00:00",
      "lastBriefId": BRIEF_ID, "lastTrigger": "slot", "lastError": "ai-agent: HTTP 404 (no /brief/macro)"},
     ("2026-09-11T20:31:10+00:00", "slot", "ai-agent: HTTP 404 (no /brief/macro)")),
], ids=["before-lifespan", "generated", "not-ready", "failed-after-a-brief"])
def test_health_reports_brief_status(client_with, brief_status, expected):
    """lastBriefAt / lastBriefTrigger describe the last stored brief; lastBriefError the last attempt."""
    body = client_with(brief_status=brief_status).get("/health").json()
    assert tuple(body[k] for k in HEALTH_FIELDS) == expected
    assert "macroBriefEnabled" in body and not {"lastBriefId", "lastAttemptAt"} & set(body)


# ── POST /macro/brief/generate (commit 3) ────────────────────────

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import macro_brief
from tests.fake_ai_agent import FakeAiAgent
from tests.test_macro_brief_flow import BriefStore, inputs_doc, not_ready, patch_inputs


@pytest.fixture
def post_with(client_with, monkeypatch):
    """POST over stubbed state with the flag on → (client, agent, inputs log)."""
    monkeypatch.setattr(main.settings, "macro_brief_enabled", True)

    def _make(store, *answers, doc=None, lock=None):
        client = client_with(store, brief_status=macro_brief.initial_brief_status())
        agent, log = FakeAiAgent(*answers), []
        patch_inputs(monkeypatch, log, doc or inputs_doc())
        for name, value in (("brief_lock", lock or asyncio.Lock()), ("ai_agent_client", agent.client()),
                            ("fred_client", object()), ("inputs_http", object())):
            monkeypatch.setattr(main.app.state, name, value, raising=False)
        return client, agent, log
    return _make


def test_generate_endpoint_disabled_503(client_with, monkeypatch):
    monkeypatch.setattr(main.settings, "macro_brief_enabled", False)
    agent, log = FakeAiAgent(), []
    patch_inputs(monkeypatch, log, inputs_doc())
    monkeypatch.setattr(main.app.state, "ai_agent_client", agent.client(), raising=False)
    for pool in (BriefStore(fail_read=AssertionError("read with the flag off")), None):
        resp = client_with(pool).post("/macro/brief/generate")
        assert (resp.status_code, resp.json()) == (503, {"detail": "macro brief disabled"})
    assert (agent.requests, log) == ([], [])
    assert "POST /macro/brief/generate" in client_with().get("/").json()["endpoints"]


def test_generate_endpoint_conflict_and_cooldown(post_with):
    client, agent, log = post_with(BriefStore(), lock=SimpleNamespace(locked=lambda: True))
    resp = client.post("/macro/brief/generate")
    assert (resp.status_code, resp.json(), agent.requests, log) == (409, {"detail": "generation in progress"}, [], [])

    store = BriefStore([{"generated_at": NOW - timedelta(seconds=599), "trigger": "slot"}])   # any trigger counts
    client, agent, log = post_with(store)
    resp = client.post("/macro/brief/generate")
    assert (resp.status_code, resp.headers["retry-after"], agent.requests, log) == (429, "1", [], [])
    store.rows[0]["generated_at"] = NOW - timedelta(seconds=601)
    assert client.post("/macro/brief/generate").status_code == 201
    resp = client.post("/macro/brief/generate")                    # the manual brief just stored counts too
    assert (resp.status_code, resp.headers["retry-after"], len(agent.requests), len(store.rows)) == (429, "600", 1, 2)


def test_generate_endpoint_outcomes(post_with, monkeypatch):
    client, agent, _ = post_with(BriefStore())
    resp = client.post("/macro/brief/generate")
    body = resp.json()
    assert (resp.status_code, list(body)) == (201, list(BODY))
    assert (body["trigger"], body["generatedAt"], body["ageMinutes"], body["brief"], body["freshness"]) == (
        "manual", NOW.isoformat(), 0, brief(), inputs_doc()["freshness"])
    assert main.app.state.brief_status["lastBriefId"] == body["id"]
    assert client.get("/macro/brief").json() == body               # one serializer: the 201 and GET never drift

    client, agent, _ = post_with(BriefStore(), doc=not_ready())
    resp = client.post("/macro/brief/generate")
    assert (resp.status_code, resp.json(), agent.requests) == (422, {"detail": "inputs not ready"}, [])
    assert main.app.state.brief_status["lastError"] == "inputs not ready"

    for store, answer, status, detail in (
        (BriefStore(), (503, {"detail": "busy"}), 502, "ai-agent: HTTP 503"),
        (BriefStore(fail_insert=asyncpg.InterfaceError("lost")), (200, brief()), 503, "brief lost: database unavailable"),
        (None, (200, brief()), 503, "database unavailable"),
        (BriefStore(fail_read=TimeoutError()), (200, brief()), 503, "database unavailable"),
    ):
        resp = post_with(store, answer)[0].post("/macro/brief/generate")
        assert (resp.status_code, resp.json()) == (status, {"detail": detail}), detail

    async def lost_race(state, client, *, trigger, clock):          # another generation took the lock first
        return {"outcome": "skipped", "cause": "busy", "id": None, "row": None}
    monkeypatch.setattr(macro_brief, "generate_once", lost_race)
    resp = post_with(BriefStore())[0].post("/macro/brief/generate")
    assert (resp.status_code, resp.json()) == (409, {"detail": "generation in progress"})
