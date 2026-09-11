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


# ── The regime trigger (decision 5, commit 6) ────────────────────

from tests.test_scheduler import MARKET_AT, YESTERDAY_SETTLE, make_state, patch_compute


def patch_publish(monkeypatch, log, published):
    """run_check's publish step answers `published`, or raises it."""
    async def fake(r, health, trend, **kwargs):
        log.append("publish")
        if isinstance(published, Exception):
            raise published
        return published

    monkeypatch.setattr(scheduler, "publish_health", fake)


@pytest.mark.asyncio
@pytest.mark.parametrize("published, queued", [
    ({"published": True, "reason": "regime_change"}, ["regime_change"]),
    ({"published": True, "reason": "critical"}, ["critical"]),
    ({"published": True, "reason": "initial"}, []),
    ({"published": True, "reason": "score_move"}, []),
    ({"published": False, "reason": None}, []),
    (RuntimeError("publish bug"), []),
], ids=["regime_change", "critical", "initial", "score_move", "nothing", "publish-raises"])
async def test_run_check_requests_brief_on_regime_change_and_critical(monkeypatch, published, queued):
    log, fired = [], []
    state = make_state(log, rows=[YESTERDAY_SETTLE])
    state.brief_queue = asyncio.Queue(maxsize=1)
    patch_compute(monkeypatch, log, 72)
    patch_publish(monkeypatch, log, published)

    def hook(hook_state, reason):                                     # what the lifespan wires: request_brief
        log.append("hook")
        fired.append(reason)
        macro_brief.request_brief(hook_state, reason)

    monkeypatch.setattr(scheduler, "on_check_published", hook)
    await scheduler.run_check(state, "market", clock=Clock(MARKET_AT))
    did_publish = isinstance(published, dict) and published["published"]
    assert log == ["compute", "publish"] + ["hook"] * did_publish + ["insert"]   # once, after publish, before insert
    assert fired == ([published["reason"]] if did_publish else [])
    assert [state.brief_queue.get_nowait() for _ in range(state.brief_queue.qsize())] == queued


@pytest.mark.asyncio
async def test_request_brief_queue_bounded_drop_if_full(monkeypatch, caplog):
    assert scheduler.on_check_published is None                      # flag off: run_check's cost is this None check
    state = SimpleNamespace(brief_queue=None)
    macro_brief.request_brief(state, "critical")                     # no queue: nothing
    state.brief_queue = asyncio.Queue(maxsize=1)
    with caplog.at_level(logging.DEBUG, logger="macro_brief"):
        macro_brief.request_brief(state, "regime_change")            # empty: queued
        for _ in range(3):
            macro_brief.request_brief(state, "critical")             # full: dropped, DEBUG only
    assert (state.brief_queue.qsize(), state.brief_queue.get_nowait()) == (1, "regime_change")
    assert own_records(caplog, logging.DEBUG) == [
        ("DEBUG", "Macro brief request (critical) dropped: one is already queued")] * 3

    class BrokenQueue:
        def put_nowait(self, item):
            raise RuntimeError("queue bug")

    log = []
    state = make_state(log, rows=[YESTERDAY_SETTLE])
    state.brief_queue = BrokenQueue()
    patch_compute(monkeypatch, log, 72)
    patch_publish(monkeypatch, log, {"published": True, "reason": "regime_change"})
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        for hook in (macro_brief.request_brief, lambda s, reason: 1 / 0):   # put_nowait raises; the hook raises
            monkeypatch.setattr(scheduler, "on_check_published", hook)
            result = await scheduler.run_check(state, "market", clock=Clock(MARKET_AT))
            assert log[-1] == "insert"                                # never raises into run_check
    assert [r.getMessage() for r in caplog.records] == [
        "Macro brief request (regime_change) raised RuntimeError: queue bug",
        "Health check publish hook raised ZeroDivisionError: division by zero"]
    assert result["errors"] == ["publish hook: ZeroDivisionError"]


@pytest.mark.asyncio
async def test_regime_brief_debounced_30_min(monkeypatch, caplog):
    stored_at = NOW - timedelta(minutes=29)
    r = rig(monkeypatch, rows=[{"generated_at": stored_at, "trigger": "manual"}])     # any trigger counts
    r.state.brief_status["lastError"] = "ai-agent: HTTP 503"
    with caplog.at_level(logging.INFO, logger="macro_brief"):
        result = await macro_brief.generate_once(r.state, r.client, trigger="regime_change", clock=Clock(NOW))
    assert result == {"outcome": "skipped", "cause": "debounced", "id": None, "row": None}
    assert (r.calls, r.agent.requests, r.state.brief_status["lastError"]) == ([], [], "ai-agent: HTTP 503")
    assert own_records(caplog, logging.INFO)[-1] == (
        "INFO", f"Macro brief (regime_change) debounced: a brief was stored at {stored_at.isoformat()}")

    r = rig(monkeypatch, rows=[{"generated_at": NOW - timedelta(minutes=31), "trigger": "slot"}])
    result = await macro_brief.generate_once(r.state, r.client, trigger="regime_change", clock=Clock(NOW))
    assert result["outcome"] == "generated"

    # A failed attempt 1 min ago stored nothing, so it debounces nothing; process memory does not count either.
    r = rig(monkeypatch, answers=((503, {"detail": "busy"}), (200, brief())))
    first = await macro_brief.generate_once(r.state, r.client, trigger="regime_change",
                                            clock=Clock(NOW - timedelta(minutes=1)))
    r.state.brief_status["lastBriefAt"] = (NOW - timedelta(minutes=1)).isoformat()
    second = await macro_brief.generate_once(r.state, r.client, trigger="regime_change", clock=Clock(NOW))
    assert (first["outcome"], second["outcome"], len(r.agent.requests)) == ("failed", "generated", 2)

    # The debounce read fails: skipped, never a guess.
    r = rig(monkeypatch, fail_read=asyncpg.InterfaceError("connection lost"))
    result = await macro_brief.generate_once(r.state, r.client, trigger="regime_change", clock=Clock(NOW))
    assert (result["cause"], r.calls, r.agent.requests, r.state.brief_status["lastError"]) == (
        "database unavailable", [], [], "database unavailable")


@pytest.mark.asyncio
async def test_critical_brief_capped_60_min(monkeypatch):
    for rows, outcome in (([{"generated_at": NOW - timedelta(minutes=59), "trigger": "critical"}], "skipped"),
                          ([{"generated_at": NOW - timedelta(minutes=61), "trigger": "critical"}], "generated"),
                          ([{"generated_at": NOW - timedelta(minutes=5), "trigger": "slot"},
                            {"generated_at": NOW - timedelta(minutes=20), "trigger": "regime_change"}], "generated")):
        r = rig(monkeypatch, rows=rows)
        result = await macro_brief.generate_once(r.state, r.client, trigger="critical", clock=Clock(NOW))
        assert (result["outcome"], len(r.agent.requests)) == (outcome, int(outcome == "generated")), rows
    assert macro_brief.DEBOUNCE == {"regime_change": (timedelta(minutes=30), None),
                                    "critical": (timedelta(minutes=60), "critical")}


@pytest.mark.asyncio
async def test_brief_loop_consumes_queue_between_slots(monkeypatch, caplog):
    # The default wait: a queued request at once, else None when the chunk ends; no queue is a plain sleep.
    state = SimpleNamespace(brief_queue=asyncio.Queue(maxsize=1))
    wait = macro_brief.queue_wait(state)
    assert await wait(0.01) is None
    macro_brief.request_brief(state, "critical")
    assert await asyncio.wait_for(wait(60), 1) == "critical"
    assert await macro_brief.queue_wait(SimpleNamespace(brief_queue=None))(0.01) is None

    # In the loop: requests at 10:00 and 11:00 generate at once (the second raises: ERROR, the loop goes on),
    # and the 12:30 slot still runs on time.
    r = rig(monkeypatch)
    r.state.brief_queue = asyncio.Queue(maxsize=1)
    runs, clock = record_generations(monkeypatch, raise_on=(2,)), Clock(et(2026, 9, 10, 9, 59))
    chunk, waits = loop_wait(clock, stop_at=et(2026, 9, 10, 12, 31))
    publishes = {et(2026, 9, 10, 10, 0): "regime_change", et(2026, 9, 10, 11, 0): "critical"}

    async def wait(seconds):
        if clock.t in publishes:                                      # run_check's hook firing mid-wait
            macro_brief.request_brief(r.state, publishes.pop(clock.t))
        return r.state.brief_queue.get_nowait() if not r.state.brief_queue.empty() else await chunk(seconds)

    with caplog.at_level(logging.ERROR, logger="macro_brief"), pytest.raises(asyncio.CancelledError):
        await macro_brief.run_brief_loop(r.state, r.client, clock=clock, wait=wait)
    assert runs == [("regime_change", et(2026, 9, 10, 10, 0)), ("critical", et(2026, 9, 10, 11, 0)),
                    ("slot", et(2026, 9, 10, 12, 30))]
    assert own_records(caplog, logging.ERROR) == [
        ("ERROR", "Macro brief request (critical) raised RuntimeError: generation bug")]
    assert r.state.brief_status["lastError"] == "request raised: RuntimeError" and max(waits) <= 60
