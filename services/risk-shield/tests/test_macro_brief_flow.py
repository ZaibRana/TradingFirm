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
        if sql == db.SLOT_BRIEF_EXISTS_SQL:
            return any(r["trigger"] == "slot" and args[0] <= r["generated_at"] < args[1] for r in self.rows)
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


# ── The schedule (decision 3, commit 5) ──────────────────────────

from datetime import date
from zoneinfo import ZoneInfo

import scheduler

ET = ZoneInfo("America/New_York")


def et(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET).astimezone(timezone.utc)


def loop_wait(clock, *, stop_at, freeze=None):
    """A fake chunk wait moving the fake clock; the chunk that would reach
    `stop_at` cancels instead. freeze=(chunk start, wake): that chunk ends at
    `wake` (the host slept through it, or it woke early)."""
    waits = []

    async def wait(seconds):
        waits.append(seconds)
        if clock.t + timedelta(seconds=seconds) >= stop_at:
            raise asyncio.CancelledError
        clock.t = freeze[1] if freeze and clock.t == freeze[0] else clock.t + timedelta(seconds=seconds)

    return wait, waits


def record_generations(monkeypatch, *, raise_on=()):
    runs = []

    async def fake(state, client, *, trigger, clock):
        runs.append((trigger, clock()))
        if len(runs) in raise_on:
            raise RuntimeError("generation bug")

    monkeypatch.setattr(macro_brief, "generate_once", fake)
    return runs


def test_brief_slots_follow_xnys_sessions():
    thursday = [et(2026, 9, 10, 7, 30), et(2026, 9, 10, 12, 30), et(2026, 9, 10, 16, 30)]
    assert macro_brief.brief_slots_for_day(date(2026, 9, 10)) == thursday
    assert (thursday[0].hour, macro_brief.brief_slots_for_day(date(2026, 11, 10))[0].hour) == (11, 12)  # across DST
    assert macro_brief.brief_slots_for_day(date(2026, 9, 12)) == []                  # Saturday
    assert macro_brief.brief_slots_for_day(date(2026, 11, 26)) == []                 # Thanksgiving
    early = macro_brief.brief_slots_for_day(date(2026, 11, 27))                      # a 13:00 close keeps all three
    assert early == [et(2026, 11, 27, 7, 30), et(2026, 11, 27, 12, 30), et(2026, 11, 27, 16, 30)]
    assert early[1] < scheduler.session_bounds(date(2026, 11, 27))[1] < early[2]
    assert macro_brief.next_brief_slot_after(et(2026, 11, 25, 16, 30)) == et(2026, 11, 27, 7, 30)
    assert macro_brief.next_brief_slot_after(et(2026, 9, 11, 16, 31)) == et(2026, 9, 14, 7, 30)
    assert macro_brief.brief_slots_between(et(2026, 9, 11, 12, 30), et(2026, 9, 14, 7, 30)) == [
        et(2026, 9, 11, 12, 30), et(2026, 9, 11, 16, 30), et(2026, 9, 14, 7, 30)]
    assert [t.strftime("%H:%M") for t in macro_brief.SLOT_TIMES_ET] == ["07:30", "12:30", "16:30"]
    assert macro_brief.BRIEF_GRACE_SECONDS == 1800


THU_0700, THU_0729 = et(2026, 9, 10, 7, 0), et(2026, 9, 10, 7, 29)


@pytest.mark.asyncio
@pytest.mark.parametrize("boot, freeze, stop_at, runs, warning", [
    (THU_0700, None, et(2026, 9, 10, 7, 31), [et(2026, 9, 10, 7, 30)], None),
    (THU_0700, (THU_0729, et(2026, 9, 10, 7, 29, 30)), et(2026, 9, 10, 7, 31), [et(2026, 9, 10, 7, 30)], None),
    (THU_0700, (THU_0729, et(2026, 9, 10, 7, 59)), et(2026, 9, 10, 8, 0), [et(2026, 9, 10, 7, 59)], None),
    (THU_0700, (THU_0729, et(2026, 9, 10, 8, 1)), et(2026, 9, 10, 8, 2), [],
     "Macro brief: 1 slot(s) up to 2026-09-10T11:30:00+00:00 skipped, not caught up (woke 1860s after it)"),
    (THU_0700, (THU_0729, et(2026, 9, 10, 13, 10)), et(2026, 9, 10, 13, 11), [],
     "Macro brief: 2 slot(s) up to 2026-09-10T16:30:00+00:00 skipped, not caught up (woke 2400s after it)"),
    (et(2026, 9, 10, 17, 7), (et(2026, 9, 10, 17, 45), et(2026, 9, 11, 9, 45)), et(2026, 9, 11, 9, 46), [],
     "Macro brief: 1 slot(s) up to 2026-09-11T11:30:00+00:00 skipped, not caught up (woke 8100s after it)"),
], ids=["exact", "early-wake", "29-min-late", "31-min-late", "slept-past-two", "a-16h-chunk"])
async def test_brief_loop_one_generation_per_slot_no_catch_up(monkeypatch, caplog, boot, freeze, stop_at, runs,
                                                             warning):
    r = rig(monkeypatch)
    generations = record_generations(monkeypatch)
    clock = Clock(boot)
    wait, waits = loop_wait(clock, stop_at=stop_at, freeze=freeze)
    with caplog.at_level(logging.WARNING, logger="macro_brief"), pytest.raises(asyncio.CancelledError):
        await macro_brief.run_brief_loop(r.state, r.client, clock=clock, wait=wait)
    assert generations == [("slot", t) for t in runs]
    assert [message for _, message in own_records(caplog)] == ([warning] if warning else [])
    assert max(waits) <= 60                                          # item 7: no wait over 60 s


@pytest.mark.asyncio
async def test_brief_loop_restart_at_0745_skips_generated_slot(monkeypatch, caplog):
    r = rig(monkeypatch, rows=[{"id": uuid.uuid4(), "generated_at": et(2026, 9, 10, 7, 31, 10), "trigger": "slot"}])
    clock = Clock(et(2026, 9, 10, 7, 45))                           # the container restarted, 15 min into the grace
    wait, _ = loop_wait(clock, stop_at=et(2026, 9, 10, 12, 31))
    with caplog.at_level(logging.INFO, logger="macro_brief"), pytest.raises(asyncio.CancelledError):
        await macro_brief.run_brief_loop(r.state, r.client, clock=clock, wait=wait)
    assert len(r.agent.requests) == 1                                # 12:30's only: none at 07:45
    assert [(row["trigger"], row["generated_at"]) for row in r.store.rows] == [
        ("slot", et(2026, 9, 10, 7, 31, 10)), ("slot", et(2026, 9, 10, 12, 30))]
    assert ("INFO", "Macro brief slot 2026-09-10T11:30:00+00:00 already has a brief: skipped") in own_records(
        caplog, logging.INFO)


@pytest.mark.asyncio
async def test_slot_brief_exists_window(monkeypatch, caplog):
    start = et(2026, 9, 10, 7, 30)
    assert macro_brief.slot_window(start) == (start, start + timedelta(seconds=1980))
    for row, generates in (({"generated_at": start + timedelta(seconds=1979), "trigger": "slot"}, False),
                           ({"generated_at": start + timedelta(seconds=1980), "trigger": "slot"}, True),
                           ({"generated_at": et(2026, 9, 10, 7, 20), "trigger": "manual"}, True),
                           ({"generated_at": et(2026, 9, 10, 7, 40), "trigger": "manual"}, True),
                           ({"generated_at": et(2026, 9, 9, 16, 31, 10), "trigger": "slot"}, True)):
        r = rig(monkeypatch, rows=[row])
        runs = record_generations(monkeypatch)
        await macro_brief.run_slot(r.state, r.client, start, clock=Clock(start + timedelta(minutes=15)))
        assert bool(runs) is generates, row

    r = rig(monkeypatch, fail_read=asyncpg.InterfaceError("connection lost"))
    runs = record_generations(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="macro_brief"):
        await macro_brief.run_slot(r.state, r.client, start, clock=Clock(start))
    assert (runs, r.state.brief_status["lastError"]) == ([], "database unavailable")
    assert own_records(caplog) == [
        ("WARNING", "Macro brief slot 2026-09-10T11:30:00+00:00 skipped: the slot check failed (InterfaceError)")]


@pytest.mark.asyncio
async def test_flag_on_ai_agent_404_warns_each_slot_no_backoff(monkeypatch, caplog):
    not_found = (404, {"detail": "Not Found"})
    r = rig(monkeypatch, answers=(not_found, not_found, not_found, (200, brief())))
    real, errors = macro_brief.generate_once, []

    async def watched(state, client, *, trigger, clock):
        result = await real(state, client, trigger=trigger, clock=clock)
        errors.append(state.brief_status["lastError"])
        return result

    monkeypatch.setattr(macro_brief, "generate_once", watched)
    clock = Clock(THU_0700)
    wait, waits = loop_wait(clock, stop_at=et(2026, 9, 11, 7, 31))
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await macro_brief.run_brief_loop(r.state, r.client, clock=clock, wait=wait)
    assert len(r.agent.requests) == 4                                # every slot calls again: no cooldown or backoff
    assert errors == ["ai-agent: HTTP 404 (no /brief/macro)"] * 3 + [None]
    assert [(row["trigger"], row["generated_at"]) for row in r.store.rows] == [("slot", et(2026, 9, 11, 7, 30))]
    assert [(rec.levelname, rec.getMessage()) for rec in caplog.records] == [
        ("WARNING", "ai-agent has no /brief/macro (HTTP 404)")] * 3  # one WARNING per slot, nothing else
    assert max(waits) == 60


@pytest.mark.asyncio
async def test_brief_loop_survives_exception_and_cancels_cleanly(monkeypatch, caplog):
    # Cancel during an ai-agent call: booted on a slot, the answer never comes.
    r = rig(monkeypatch)
    r.agent.delay = 30
    task = asyncio.create_task(macro_brief.run_brief_loop(r.state, r.client, clock=Clock(et(2026, 9, 10, 7, 30))))
    for _ in range(200):
        if r.agent.requests:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (len(r.agent.requests), r.store.rows, r.state.brief_lock.locked()) == (1, [], False)

    # Cancel during a wait: Saturday noon, the real chunked sleep.
    task = asyncio.create_task(macro_brief.run_brief_loop(r.state, r.client, clock=Clock(et(2026, 9, 12, 12, 0))))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # A generation raises: ERROR, and the next slot still runs.
    runs, clock = record_generations(monkeypatch, raise_on=(1,)), Clock(THU_0700)
    wait, _ = loop_wait(clock, stop_at=et(2026, 9, 10, 12, 31))
    with caplog.at_level(logging.ERROR, logger="macro_brief"), pytest.raises(asyncio.CancelledError):
        await macro_brief.run_brief_loop(r.state, r.client, clock=clock, wait=wait)
    assert runs == [("slot", et(2026, 9, 10, 7, 30)), ("slot", et(2026, 9, 10, 12, 30))]
    assert own_records(caplog, logging.ERROR) == [("ERROR", "Macro brief slot raised RuntimeError: generation bug")]
    assert r.state.brief_status["lastError"] == "slot raised: RuntimeError"

    # No next slot: a 300 s wait in ≤ 60 s chunks, then it looks again.
    real_next, looked = macro_brief.next_brief_slot_after, []
    monkeypatch.setattr(macro_brief, "next_brief_slot_after",
                        lambda now: looked.append(now) or (real_next(now) if len(looked) > 1 else None))
    runs, clock = record_generations(monkeypatch), Clock(THU_0700)
    wait, waits = loop_wait(clock, stop_at=et(2026, 9, 10, 7, 31))
    with pytest.raises(asyncio.CancelledError):
        await macro_brief.run_brief_loop(r.state, r.client, clock=clock, wait=wait)
    assert (waits[:5], looked[:2], runs) == ([60] * 5, [THU_0700, et(2026, 9, 10, 7, 5)],
                                             [("slot", et(2026, 9, 10, 7, 30))])
