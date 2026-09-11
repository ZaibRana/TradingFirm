"""
TradingFirm — macro brief generation (Part 3.6b).

generate_once (spec decision 2) is one generation: busy → no pool → inputs →
ready → ai-agent → insert → brief_status. A row is stored only for a valid
ai-agent answer on ready inputs, and nothing retries: the next slot, regime
request or manual call is independent. Every function takes a `clock`;
nothing reads the time directly.

run_brief_loop (decision 3) generates at 07:30, 12:30 and 16:30 ET on XNYS
sessions, waiting through wallclock in chunks of ≤ 60 s, never catching up.

brief_status is process memory and small (G8): the inputs document is never
kept in state.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional

import ai_agent_client
import db
import macro_inputs
import scheduler
import wallclock
from ai_agent_client import AiAgentBadResponse, AiAgentError
from scoring.alert_manager import REASON_CRITICAL, REASON_REGIME_CHANGE

logger = logging.getLogger(__name__)

GENERATED, SKIPPED, FAILED = "generated", "skipped", "failed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def initial_brief_status() -> dict:
    """app.state.brief_status before any attempt. lastBriefAt, lastBriefId and
    lastTrigger describe the last stored brief; lastError the last attempt."""
    return {"lastAttemptAt": None, "lastBriefAt": None, "lastBriefId": None, "lastTrigger": None,
            "lastError": None}


def health_regime_score(health: dict) -> tuple[Optional[str], Optional[int]]:
    """The inputs' latest regime and score, or lastScored's when the score is null."""
    if health.get("score") is not None:
        return health.get("regime"), health["score"]
    scored = health.get("lastScored") or {}
    return scored.get("regime"), scored.get("score")


def _not_stored(status: dict, outcome: str, cause: str) -> dict:
    status["lastError"] = cause
    return {"outcome": outcome, "cause": cause, "id": None, "row": None}


async def generate_once(state, client, *, trigger: str, clock: Callable[[], datetime] = _utc_now) -> dict:
    """
    One generation on `state` (brief_lock, brief_status, db_pool, fred_client,
    inputs_http, …) with an AiAgentClient. Returns {outcome, cause, id, row}:
    generated (row = the stored columns, shaped as db.latest_macro_brief reads
    them), skipped (busy / database unavailable / inputs not ready) or failed
    (ai-agent: … / insert: …). A dependency failure never raises; a bug does,
    for the loop (ERROR) or the endpoint (500). Cancellation propagates, and
    nothing is stored mid-call.
    """
    if state.brief_lock.locked():            # checked without waiting: never queue behind a 180 s call
        logger.info(f"Macro brief ({trigger}) skipped: a generation is running")
        return {"outcome": SKIPPED, "cause": "busy", "id": None, "row": None}
    async with state.brief_lock:
        status = state.brief_status
        status["lastAttemptAt"] = clock().isoformat()
        pool = getattr(state, "db_pool", None)
        if pool is None:                     # never spend an LLM call that cannot be stored
            logger.warning(f"Macro brief ({trigger}) skipped: database unavailable")
            return _not_stored(status, SKIPPED, "database unavailable")

        # Regime requests are debounced by stored briefs read from Postgres (decision 5): a
        # failed attempt never debounces the next one, and a restart forgets nothing.
        if trigger in DEBOUNCE:
            window, counted = DEBOUNCE[trigger]
            try:
                last = await db.last_brief_at(pool, counted)
            except db.DB_FAILURES as e:
                logger.warning(f"Macro brief ({trigger}) skipped: the debounce read failed ({type(e).__name__})")
                return _not_stored(status, SKIPPED, "database unavailable")
            if last is not None and clock() - last < window:
                logger.info(f"Macro brief ({trigger}) debounced: a brief was stored at {last.isoformat()}")
                return {"outcome": SKIPPED, "cause": "debounced", "id": None, "row": None}

        # Assembled directly, never GET /macro/brief/inputs' 60 s reuse.
        inputs = await macro_inputs.assemble_inputs(state, state.fred_client, state.inputs_http, now=clock())
        if not inputs["ready"]:
            logger.warning(f"Macro brief ({trigger}) skipped: inputs not ready "
                           f"(health {inputs['health'].get('status')}), no ai-agent call")
            return _not_stored(status, SKIPPED, "inputs not ready")

        try:
            answer = await client.brief(inputs)
        except AiAgentError as e:            # the client has logged it
            cause = f"AiAgentBadResponse ({e})" if isinstance(e, AiAgentBadResponse) else str(e)
            return _not_stored(status, FAILED, f"ai-agent: {cause}")

        regime, score = health_regime_score(inputs["health"])
        row = {"generated_at": clock(), "trigger": trigger, "regime": regime, "health_score": score,
               "brief_text": answer["oneParagraph"], "brief": answer, "inputs": inputs}
        try:
            row = {"id": await db.insert_macro_brief(pool, **row), **row}
        except db.DB_FAILURES as e:
            logger.warning(f"Macro brief lost ({trigger}): the insert failed ({type(e).__name__})")
            return _not_stored(status, FAILED, f"insert: {type(e).__name__}")

        status.update(lastBriefAt=row["generated_at"].isoformat(), lastBriefId=row["id"], lastTrigger=trigger,
                      lastError=None)
    logger.info(f"Macro brief ({trigger}) stored: {row['id']}, {regime} {score}")
    return {"outcome": GENERATED, "cause": None, "id": row["id"], "row": row}


# ── The regime trigger (decision 5) ──────────────────────────────

# trigger → (window, the trigger whose stored briefs count; None = any). The 30 min
# rule covers "a scheduled brief ran in the last 30 min"; CRITICAL has its own cap.
DEBOUNCE = {REASON_REGIME_CHANGE: (timedelta(minutes=30), None),
            REASON_CRITICAL: (timedelta(minutes=60), REASON_CRITICAL)}


def request_brief(state, reason: str) -> None:
    """scheduler.on_check_published while MACRO_BRIEF_ENABLED: queue a brief
    for a regime_change or critical publish (initial and score_move never do).
    Never awaits, never raises: a full queue drops the request (one is already
    waiting, and the debounce would absorb it), and a bug is an ERROR."""
    queue = getattr(state, "brief_queue", None)
    if reason not in DEBOUNCE or queue is None:
        return
    try:
        queue.put_nowait(reason)
    except asyncio.QueueFull:
        logger.debug(f"Macro brief request ({reason}) dropped: one is already queued")
    except Exception as e:
        logger.error(f"Macro brief request ({reason}) raised {type(e).__name__}: {e}")


def queue_wait(state) -> Callable:
    """The loop's default chunk wait: the next queued request at once, or None
    when `seconds` pass (asyncio.wait_for on the queue). No queue: a plain sleep."""
    async def wait(seconds: float) -> Optional[str]:
        queue = getattr(state, "brief_queue", None)
        if queue is None:
            await asyncio.sleep(seconds)
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=seconds)
        except asyncio.TimeoutError:
            return None
    return wait


async def _handle_request(state, client, reason: str, clock: Callable[[], datetime]) -> None:
    """A queued request: one generation now, its debounce deciding. A raise is a
    bug: ERROR, and the loop waits on toward the same slot."""
    try:
        await generate_once(state, client, trigger=reason, clock=clock)
    except Exception as e:
        logger.error(f"Macro brief request ({reason}) raised {type(e).__name__}: {e}")
        state.brief_status["lastError"] = f"request raised: {type(e).__name__}"


# ── The schedule (decision 3) ────────────────────────────────────

SLOT_TIMES_ET = (time(7, 30), time(12, 30), time(16, 30))
BRIEF_GRACE_SECONDS = 1800        # provisional: a slot runs at most this late (a laptop waking at 08:00)
FALLBACK_WAIT_SECONDS = 300       # when no next slot can be computed


def brief_slots_for_day(day: date) -> list[datetime]:
    """The three slots of an ET date, in UTC, when it is an XNYS session (early
    closes keep all three) on the scheduler's cached calendar; [] otherwise."""
    if scheduler.session_bounds(day) is None:
        return []
    return [datetime.combine(day, t, tzinfo=scheduler.ET).astimezone(timezone.utc) for t in SLOT_TIMES_ET]


def brief_slots_between(start: datetime, end: datetime) -> list[datetime]:
    """Every slot in [start, end], in order."""
    day, slots = start.astimezone(scheduler.ET).date(), []
    while day <= end.astimezone(scheduler.ET).date():
        slots += [s for s in brief_slots_for_day(day) if start <= s <= end]
        day += timedelta(days=1)
    return slots


def next_brief_slot_after(now: datetime) -> Optional[datetime]:
    """The first slot strictly after `now` within the scheduler's 15-day
    horizon, or None (ERROR)."""
    day = now.astimezone(scheduler.ET).date()
    try:
        for offset in range(scheduler.NEXT_SLOT_HORIZON_DAYS):
            for start in brief_slots_for_day(day + timedelta(days=offset)):
                if start > now:
                    return start
    except scheduler.CalendarOutOfBounds as e:
        logger.error(f"No next macro brief slot: {e}")
        return None
    logger.error(f"No macro brief slot within {scheduler.NEXT_SLOT_HORIZON_DAYS} days of {now.isoformat()}")
    return None


def slot_window(start: datetime) -> tuple[datetime, datetime]:
    """Where every row a slot can produce lands: its generation starts within
    the grace and ends within the ai-agent timeout, so [start, start + 1,980 s)."""
    return start, start + timedelta(seconds=BRIEF_GRACE_SECONDS + ai_agent_client.BRIEF_TIMEOUT)


async def run_slot(state, client, start: datetime, *, clock: Callable[[], datetime]) -> None:
    """One due slot. A `slot` brief already in its window (a restart) skips it,
    and so does a failed read: never a second brief on a guess. No pool is
    generate_once's to decide."""
    pool = getattr(state, "db_pool", None)
    if pool is not None:
        try:
            exists = await db.slot_brief_exists(pool, *slot_window(start))
        except db.DB_FAILURES as e:
            logger.warning(f"Macro brief slot {start.isoformat()} skipped: the slot check failed ({type(e).__name__})")
            state.brief_status["lastError"] = "database unavailable"
            return
        if exists:
            logger.info(f"Macro brief slot {start.isoformat()} already has a brief: skipped")
            return
    await generate_once(state, client, trigger="slot", clock=clock)


async def _handle_wake(state, client, slot: datetime, clock: Callable[[], datetime]) -> None:
    """Awake at or past `slot`: the latest slot passed is the one that counts,
    run when at most BRIEF_GRACE_SECONDS late. The other passed slots, and that
    one when later, get one WARNING and are never caught up. A raise is a bug:
    ERROR, and the loop goes on."""
    try:
        now = clock()
        passed = brief_slots_between(slot, now)
        late = (now - passed[-1]).total_seconds()
        missed = passed if late > BRIEF_GRACE_SECONDS else passed[:-1]
        if missed:
            logger.warning(f"Macro brief: {len(missed)} slot(s) up to {missed[-1].isoformat()} skipped, "
                           f"not caught up (woke {(now - missed[-1]).total_seconds():.0f}s after it)")
        if late <= BRIEF_GRACE_SECONDS:
            await run_slot(state, client, passed[-1], clock=clock)
    except Exception as e:
        logger.error(f"Macro brief slot raised {type(e).__name__}: {e}")
        state.brief_status["lastError"] = f"slot raised: {type(e).__name__}"


async def run_brief_loop(state, client, *, clock: Callable[[], datetime] = _utc_now, wait=None) -> None:
    """
    Forever: wait for the next slot in repeated wait(wallclock.wait_seconds(
    target, clock())) chunks of ≤ 60 s, the clock re-read after each, so a Mac
    sleep is noticed within one chunk of waking (v2 item 7); then handle the
    wake. A chunk that answers a queued request (default wait: queue_wait)
    generates it at once and waits on toward the same slot. At boot only a
    slot whose grace still holds counts (a restart at 07:45 checks 07:30). No
    next slot is an ERROR and a 300 s wait through the same chunks.
    Cancellation (shutdown) propagates.
    """
    wait = wait or queue_wait(state)
    logger.info("Macro brief loop running")
    booting = True
    while True:
        try:
            now = clock()
            in_grace = brief_slots_between(now - timedelta(seconds=BRIEF_GRACE_SECONDS), now) if booting else []
            slot = in_grace[-1] if in_grace else next_brief_slot_after(now)
        except Exception as e:
            logger.error(f"Macro brief loop error {type(e).__name__}: {e}")
            slot = None
        booting = False
        target = slot or clock() + timedelta(seconds=FALLBACK_WAIT_SECONDS)
        while (seconds := wallclock.wait_seconds(target, clock())) > 0:
            reason = await wait(seconds)
            if reason is not None:
                await _handle_request(state, client, reason, clock)
        if slot is not None:
            await _handle_wake(state, client, slot, clock)
