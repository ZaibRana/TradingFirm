"""Part 3.6b — one generation (spec decision 2). assemble_inputs is patched to
fixed documents, Postgres is db.py's real helpers over BriefStore, ai-agent is
tests/fake_ai_agent.py. No socket."""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import asyncpg
import httpx
import pytest

import db
import macro_brief
import macro_inputs
from tests.fake_ai_agent import FakeAiAgent, brief

NOW = datetime(2026, 9, 11, 20, 30, tzinfo=timezone.utc)         # the 16:30 ET slot


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class BriefStore:
    """risk.macro_briefs in memory behind db.py's real helpers, answering by
    their literal SQL (the text is pinned in test_db.py). "insert" goes on
    `log` per stored row; fail_insert / fail_read raise on the insert / reads."""

    def __init__(self, rows=(), *, log=None, fail_insert=None, fail_read=None):
        self.rows, self.log = [dict(r) for r in rows], [] if log is None else log
        self.fail_insert, self.fail_read = fail_insert, fail_read

    async def fetchval(self, sql, *args):
        if sql == db.INSERT_MACRO_BRIEF_SQL:
            if self.fail_insert:
                raise self.fail_insert
            at, regime, score, text, brief_json, inputs_json, trigger = args
            self.rows.append({"id": uuid.uuid4(), "generated_at": at, "trigger": trigger, "regime": regime,
                              "health_score": score, "brief_text": text, "brief": json.loads(brief_json),
                              "inputs": json.loads(inputs_json)})
            self.log.append("insert")
            return self.rows[-1]["id"]
        if self.fail_read:
            raise self.fail_read
        if sql == db.LAST_BRIEF_AT_SQL:
            return max((r["generated_at"] for r in self.rows if args[0] in (None, r["trigger"])), default=None)
        raise AssertionError(f"unexpected query: {sql}")

    async def fetchrow(self, sql, *args):
        if self.fail_read:
            raise self.fail_read
        if sql == db.LATEST_MACRO_BRIEF_SQL:
            return max(self.rows, key=lambda r: r["generated_at"], default=None)
        raise AssertionError(f"unexpected query: {sql}")

    async def execute(self, *args):
        raise AssertionError("briefs are stored through fetchval (RETURNING id)")


def inputs_doc(*, ready=True, status="ok", score=64, regime="CAUTIOUS", last_scored=None):
    health = {"status": status, "score": score, "regime": regime}
    if score is None:
        health["lastScored"] = last_scored
    return {"schemaVersion": 1, "assembledAt": NOW.isoformat(), "ready": ready, "health": health,
            "news": {"status": "ok", "hours": 25, "items": []}, "freshness": {"anyStale": False}}


def not_ready(status="no_checks"):
    return inputs_doc(ready=False, status=status, score=None, regime=None)


def patch_inputs(monkeypatch, log, *docs):
    """assemble_inputs answers each call with the next document, the last repeating."""
    calls = []

    async def fake(state, fred_client, http, *, now):
        calls.append(now)
        log.append("inputs")
        return docs[min(len(calls), len(docs)) - 1]

    monkeypatch.setattr(macro_inputs, "assemble_inputs", fake)
    return calls


def rig(monkeypatch, *docs, answers=(), pool=True, **store_kw):
    """One generation's dependencies, sharing one event log."""
    log = []
    store, agent = BriefStore(log=log, **store_kw), FakeAiAgent(*answers, log=log)
    calls = patch_inputs(monkeypatch, log, *(docs or (inputs_doc(),)))
    state = SimpleNamespace(db_pool=store if pool else None, brief_lock=asyncio.Lock(),
                            brief_status=macro_brief.initial_brief_status(),
                            fred_client=object(), inputs_http=object(), inputs_last=None)
    return SimpleNamespace(state=state, agent=agent, client=agent.client(), store=store, log=log, calls=calls)


def not_stored(outcome, cause):
    return {"outcome": outcome, "cause": cause, "id": None, "row": None}


def own_records(caplog, level=logging.WARNING):
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "macro_brief" and r.levelno >= level]


@pytest.mark.asyncio
async def test_generate_once_stores_one_row(monkeypatch):
    clock, doc = Clock(NOW), inputs_doc()
    r = rig(monkeypatch, doc)

    class Thinking:                                                # the answer takes 70 s of the clock
        async def brief(self, inputs):
            clock.t += timedelta(seconds=70)
            return await r.client.brief(inputs)

    result = await macro_brief.generate_once(r.state, Thinking(), trigger="slot", clock=clock)
    assert (r.log, r.calls) == (["inputs", "ai-agent", "insert"], [NOW])
    assert [json.loads(req.content) for req in r.agent.requests] == [{"inputs": doc}]
    (row,) = r.store.rows
    assert row == {"id": row["id"], "generated_at": NOW + timedelta(seconds=70), "trigger": "slot",
                   "regime": "CAUTIOUS", "health_score": 64, "brief_text": brief()["oneParagraph"],
                   "brief": brief(), "inputs": doc}
    assert result == {"outcome": "generated", "cause": None, "id": str(row["id"]),
                      "row": {**row, "id": str(row["id"])}}
    assert not r.state.brief_lock.locked()


@pytest.mark.asyncio
async def test_generate_once_uses_last_scored_when_score_null(monkeypatch):
    scored = {"score": 58, "regime": "CAUTIOUS", "checkedAt": "2026-09-11T20:20:02+00:00"}
    r = rig(monkeypatch, inputs_doc(score=None, regime=None, last_scored=scored))
    assert (await macro_brief.generate_once(r.state, r.client, trigger="slot", clock=Clock(NOW)))["outcome"] == "generated"
    assert (r.store.rows[0]["regime"], r.store.rows[0]["health_score"]) == ("CAUTIOUS", 58)
    assert macro_brief.health_regime_score({"score": 71, "regime": "HEALTHY", "lastScored": scored}) == ("HEALTHY", 71)


@pytest.mark.asyncio
@pytest.mark.parametrize("doc", [not_ready("no_checks"), not_ready("ok"), not_ready("unavailable")],
                         ids=["no-checks", "no-scored-row", "health-read-failed"])
async def test_generate_once_not_ready_makes_no_call(monkeypatch, caplog, doc):
    r = rig(monkeypatch, doc)
    with caplog.at_level(logging.WARNING, logger="macro_brief"):
        result = await macro_brief.generate_once(r.state, r.client, trigger="slot", clock=Clock(NOW))
    assert result == not_stored("skipped", "inputs not ready")
    assert (r.log, r.agent.requests, r.store.rows) == (["inputs"], [], [])
    assert r.state.brief_status["lastError"] == "inputs not ready"
    assert own_records(caplog) == [("WARNING", f"Macro brief (slot) skipped: inputs not ready "
                                               f"(health {doc['health']['status']}), no ai-agent call")]


@pytest.mark.asyncio
async def test_generate_once_without_pool_makes_no_call(monkeypatch, caplog):
    r = rig(monkeypatch, pool=False)
    with caplog.at_level(logging.WARNING, logger="macro_brief"):
        result = await macro_brief.generate_once(r.state, r.client, trigger="manual", clock=Clock(NOW))
    assert result == not_stored("skipped", "database unavailable")
    assert (r.calls, r.agent.requests, r.state.brief_status["lastError"]) == ([], [], "database unavailable")
    assert own_records(caplog) == [("WARNING", "Macro brief (manual) skipped: database unavailable")]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, cause", [
    ((404, {"detail": "Not Found"}), "ai-agent: HTTP 404 (no /brief/macro)"),
    ((422, {"detail": []}), "ai-agent: HTTP 422"),
    ((503, {"detail": "busy"}), "ai-agent: HTTP 503"),
    ((200, httpx.ConnectError("refused")), "ai-agent: ConnectError"),
    ((200, brief(keyRisks=["a risk"] * 9)), "ai-agent: AiAgentBadResponse (keyRisks: 9 items)"),
], ids=["404", "422", "503", "transport", "bad-response"])
async def test_generate_once_ai_agent_failure_stores_nothing(monkeypatch, caplog, answer, cause):
    r = rig(monkeypatch, answers=(answer,))
    with caplog.at_level(logging.DEBUG):
        result = await macro_brief.generate_once(r.state, r.client, trigger="slot", clock=Clock(NOW))
    assert result == not_stored("failed", cause)
    assert (r.log, r.store.rows, r.state.brief_status["lastError"]) == (["inputs", "ai-agent"], [], cause)
    assert own_records(caplog) == []                                # the client logs it, once: nothing doubles it


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [asyncpg.InterfaceError("connection lost"), TimeoutError("command timeout")])
async def test_generate_once_insert_failure_is_reported(monkeypatch, caplog, exc):
    r = rig(monkeypatch, fail_insert=exc)
    with caplog.at_level(logging.WARNING, logger="macro_brief"):
        result = await macro_brief.generate_once(r.state, r.client, trigger="slot", clock=Clock(NOW))
    cause = f"insert: {type(exc).__name__}"
    assert result == not_stored("failed", cause)
    assert (r.log, r.store.rows, r.state.brief_status["lastError"]) == (["inputs", "ai-agent"], [], cause)
    assert own_records(caplog) == [("WARNING", f"Macro brief lost (slot): the insert failed ({type(exc).__name__})")]


@pytest.mark.asyncio
async def test_generate_once_busy_skips(monkeypatch):
    r = rig(monkeypatch)
    r.state.brief_status.update(lastAttemptAt="2026-09-11T16:30:00+00:00", lastError="ai-agent: HTTP 503")
    before = dict(r.state.brief_status)
    async with r.state.brief_lock:                                  # a 180 s call is running
        result = await asyncio.wait_for(
            macro_brief.generate_once(r.state, r.client, trigger="critical", clock=Clock(NOW)), 1)
    assert result == {"outcome": "skipped", "cause": "busy", "id": None, "row": None}
    assert (r.calls, r.agent.requests, r.state.brief_status) == ([], [], before)


@pytest.mark.asyncio
async def test_generate_once_never_uses_endpoint_reuse(monkeypatch):
    stale, fresh = inputs_doc(score=40, regime="DANGER"), inputs_doc()
    r = rig(monkeypatch, fresh)
    r.state.inputs_last = (time.monotonic() - 10, stale)            # GET /macro/brief/inputs kept this 10 s ago
    await macro_brief.generate_once(r.state, r.client, trigger="manual", clock=Clock(NOW))
    assert len(r.calls) == 1 and json.loads(r.agent.requests[0].content) == {"inputs": fresh}
    assert (r.store.rows[0]["inputs"], r.store.rows[0]["health_score"]) == (fresh, 64)
    assert r.state.inputs_last[1] is stale                          # neither read nor refreshed


@pytest.mark.asyncio
async def test_brief_status_records_outcomes(monkeypatch):
    r = rig(monkeypatch, inputs_doc(), not_ready(), inputs_doc(), inputs_doc(),
            answers=((200, brief()), (503, {"detail": "busy"}), (200, brief())))
    clock, seen = Clock(NOW), []
    for minutes, trigger in ((0, "slot"), (5, "manual"), (10, "critical"), (15, "manual")):
        clock.t = NOW + timedelta(minutes=minutes)
        await macro_brief.generate_once(r.state, r.client, trigger=trigger, clock=clock)
        seen.append(dict(r.state.brief_status))
    at = lambda m: (NOW + timedelta(minutes=m)).isoformat()
    first, second = (str(row["id"]) for row in r.store.rows)
    assert seen == [
        {"lastAttemptAt": at(0), "lastBriefAt": at(0), "lastBriefId": first, "lastTrigger": "slot", "lastError": None},
        {"lastAttemptAt": at(5), "lastBriefAt": at(0), "lastBriefId": first, "lastTrigger": "slot",
         "lastError": "inputs not ready"},
        {"lastAttemptAt": at(10), "lastBriefAt": at(0), "lastBriefId": first, "lastTrigger": "slot",
         "lastError": "ai-agent: HTTP 503"},
        {"lastAttemptAt": at(15), "lastBriefAt": at(15), "lastBriefId": second, "lastTrigger": "manual",
         "lastError": None},
    ]
    assert all(v is None or isinstance(v, str) for status in seen for v in status.values())   # never the document
