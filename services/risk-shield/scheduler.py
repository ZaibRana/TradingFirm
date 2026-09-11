"""
TradingFirm — the regime scheduler (Part 3.4).

When a health check runs (spec 3.4 decision 3), from the XNYS calendar:
  market  every 5-minute slot of the regular session, open … close inclusive
          (09:30 … 16:00 = 79 slots; 43 on an early close)
  settle  16:20 ET on every session day, early closes included. That is after
          3.3's 16:15 partial-bar cut-off, so the day's bars are complete and
          3.3's rule stays unchanged.
Nothing runs outside those slots (night mode is 3.4b).

One check (decision 4) is compute → trend base → publish → insert, each
side effect isolated, so a Postgres failure never delays a publish and a
Redis failure never stops a row.

Every function takes an aware `now` or a `clock`; nothing reads the time
directly.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

import db
import news_poller
import wallclock
from monitors import quotes
from scoring.alert_manager import publish_health
from scoring.health_calculator import compute_health

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CALENDAR_NAME = "XNYS"
SLOT_MINUTES = 5
SETTLE_TIME_ET = time(16, 20)   # provisional (spec 3.4 decision 3)
GRACE_SECONDS = 60              # provisional: a slot runs only this late
KIND_MARKET = "market"
KIND_SETTLE = "settle"

# The calendar is built around the date asked about, so one build covers
# every date the scheduler looks at for about a year.
CALENDAR_DAYS_BEFORE = 30
CALENDAR_DAYS_AFTER = 400
NEXT_SLOT_HORIZON_DAYS = 15     # the longest gap between sessions is 4 days


class CalendarOutOfBounds(RuntimeError):
    """The XNYS calendar cannot cover the date even after a rebuild."""


# One calendar per process, built lazily (decision 2). Pure data, no network.
_calendar_state: dict[str, Any] = {"cal": None}


def _build_calendar(day: date):
    return xcals.get_calendar(
        CALENDAR_NAME,
        start=day - timedelta(days=CALENDAR_DAYS_BEFORE),
        end=day + timedelta(days=CALENDAR_DAYS_AFTER),
    )


def _covers(cal, day: date) -> bool:
    ts = pd.Timestamp(day)
    return cal.first_session <= ts <= cal.last_session


def market_calendar(day: date):
    """The cached XNYS calendar if it covers `day`; otherwise one rebuild
    around `day`. Still not covering it raises CalendarOutOfBounds."""
    cal = _calendar_state["cal"]
    if cal is not None and _covers(cal, day):
        return cal
    if cal is not None:
        logger.warning(f"XNYS calendar does not cover {day}, rebuilding")
    cal = _build_calendar(day)
    _calendar_state["cal"] = cal
    if not _covers(cal, day):
        raise CalendarOutOfBounds(f"XNYS calendar cannot cover {day}")
    return cal


def _require_aware(now: Any) -> None:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduler times must be timezone-aware datetimes")


def session_bounds(day: date) -> Optional[tuple[datetime, datetime]]:
    """(open, close) in UTC for an XNYS session day, None otherwise."""
    cal = market_calendar(day)
    ts = pd.Timestamp(day)
    if not cal.is_session(ts):
        return None
    return cal.session_open(ts).to_pydatetime(), cal.session_close(ts).to_pydatetime()


def slots_for_day(day: date) -> list[tuple[str, datetime]]:
    """Every slot of an ET date, in order: the market slots, then settle."""
    bounds = session_bounds(day)
    if bounds is None:
        return []
    open_, close = bounds
    step = timedelta(minutes=SLOT_MINUTES)
    slots = []
    t = open_
    while t <= close:
        slots.append((KIND_MARKET, t))
        t += step
    settle = datetime.combine(day, SETTLE_TIME_ET, tzinfo=ET).astimezone(timezone.utc)
    slots.append((KIND_SETTLE, settle))
    return slots


def slot_for(now: datetime) -> Optional[tuple[str, datetime]]:
    """(kind, slot start) for the slot whose 5-minute window holds `now`, or
    None. Fails closed: a date the calendar cannot cover gives None + ERROR."""
    _require_aware(now)
    day = now.astimezone(ET).date()
    try:
        slots = slots_for_day(day)
    except CalendarOutOfBounds as e:
        logger.error(f"No health check slot: {e}")
        return None
    window = timedelta(minutes=SLOT_MINUTES)
    for kind, start in slots:
        if start <= now < start + window:
            return kind, start
    return None


def next_slot_after(now: datetime) -> Optional[tuple[str, datetime]]:
    """The first slot starting strictly after `now`, or None (ERROR) when
    the calendar cannot cover the dates ahead."""
    _require_aware(now)
    day = now.astimezone(ET).date()
    try:
        for offset in range(NEXT_SLOT_HORIZON_DAYS):
            for kind, start in slots_for_day(day + timedelta(days=offset)):
                if start > now:
                    return kind, start
    except CalendarOutOfBounds as e:
        logger.error(f"No next health check slot: {e}")
        return None
    logger.error(f"No health check slot within {NEXT_SLOT_HORIZON_DAYS} days of {now.isoformat()}")
    return None


def last_slot_before(now: datetime) -> Optional[tuple[str, datetime]]:
    """The latest slot starting at or before `now` (Part 3.6a decision 5:
    the check that should already have produced a row), or None (ERROR)
    when the calendar cannot cover the dates behind."""
    _require_aware(now)
    day = now.astimezone(ET).date()
    try:
        for offset in range(NEXT_SLOT_HORIZON_DAYS):
            for kind, start in reversed(slots_for_day(day - timedelta(days=offset))):
                if start <= now:
                    return kind, start
    except CalendarOutOfBounds as e:
        logger.error(f"No previous health check slot: {e}")
        return None
    logger.error(f"No health check slot within {NEXT_SLOT_HORIZON_DAYS} days before {now.isoformat()}")
    return None


# ── One check (decision 4) ───────────────────────────────────────

TREND_POINTS = 5     # provisional: ± points against the settle base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def trend_from(score: Optional[int], settle_score: Optional[int]) -> Optional[str]:
    if score is None or settle_score is None:
        return None
    delta = score - settle_score
    if delta >= TREND_POINTS:
        return "improving"
    if delta <= -TREND_POINTS:
        return "declining"
    return "stable"


def settle_cutoff(now: datetime) -> datetime:
    """The trend base must be older than this: the XNYS open of `now`'s ET
    date, so the 16:20 settle check reads an earlier session's settle and
    never its own. ET midnight on a date that is not a session."""
    _require_aware(now)
    day = now.astimezone(ET).date()
    try:
        bounds = session_bounds(day)
    except CalendarOutOfBounds:
        bounds = None
    if bounds is not None:
        return bounds[0]
    return datetime.combine(day, time(0), tzinfo=ET).astimezone(timezone.utc)


def _failure(step: str, e: Exception, errors: list[str]) -> None:
    errors.append(f"{step}: {type(e).__name__}")
    if isinstance(e, db.DB_FAILURES):
        logger.warning(f"Health check {step} failed: {e!r}")
    else:
        logger.error(f"Health check {step} raised {type(e).__name__}: {e}")


async def run_check(state, kind: str, *, clock: Callable[[], datetime] = _utc_now) -> Optional[dict]:
    """
    One health check on `state` (redis, db_pool, cooldowns, check_status):
    compute → settle base → publish → insert. Skips (None) while a quotes
    download holds the lock. A dependency failure in steps 2–4 is WARNING, a
    bug ERROR, and the next step still runs; compute itself never raises for
    a source state (3.3), so a raise there is a bug for the loop to log.
    """
    if quotes.download_in_flight():
        logger.warning(f"Health check ({kind}) skipped: a core quotes download holds the lock")
        return None

    r = getattr(state, "redis", None)
    pool = getattr(state, "db_pool", None)
    errors: list[str] = []

    health = await compute_health(r, state.cooldowns, now=clock)
    checked_at = datetime.fromisoformat(health["checkedAt"])

    settle = None
    if pool is not None:
        try:
            settle = await db.settle_base(pool, settle_cutoff(checked_at))
        except Exception as e:
            _failure("settle base read", e, errors)
    trend = trend_from(health["score"], settle["score"] if settle else None)

    published = None
    # The first check after a host pause carries it, published or not (3.4 follow-up addition 1).
    paused = getattr(state, "pending_paused_seconds", None)
    state.pending_paused_seconds = None
    try:
        published = await publish_health(r, health, trend, now=checked_at,
                                         news=news_poller.stale_view(state, checked_at),
                                         paused_seconds=paused)
    except Exception as e:
        _failure("publish", e, errors)

    if pool is None:
        logger.warning(f"Health check ({kind}) not recorded: database unavailable")
    else:
        try:
            await db.insert_health_check(pool, health, kind, trend, settle, paused_seconds=paused)
        except Exception as e:
            _failure("insert", e, errors)

    state.check_status.update(
        lastCheckAt=health["checkedAt"],
        lastKind=kind,
        lastScore=health["score"],
        lastError="; ".join(errors) or None,
    )
    logger.info(
        f"Health check ({kind}): {health['regime']} {health['score']}, trend {trend}, "
        f"published {bool(published and published['published'])}"
    )
    return {"health": health, "trend": trend, "settle": settle, "published": published, "errors": errors}


# ── The loop (decision 3) ────────────────────────────────────────

FALLBACK_SLEEP_SECONDS = 300    # when no next slot can be computed
MAX_MISSED_SCAN = 2000          # bounds the missed-slot count after a long sleep


def _missed_between(last_start: Optional[datetime], due_start: datetime) -> int:
    """Slots strictly between the last slot handled and `due_start`."""
    if last_start is None:
        return 0
    missed, cursor = 0, last_start
    for _ in range(MAX_MISSED_SCAN):
        nxt = next_slot_after(cursor)
        if nxt is None or nxt[1] >= due_start:
            break
        missed += 1
        cursor = nxt[1]
    return missed


async def _guarded_check(state, kind: str, clock: Callable[[], datetime]) -> None:
    """A raise out of a check is a bug: logged, and the loop goes on."""
    try:
        await run_check(state, kind, clock=clock)
    except Exception as e:
        logger.error(f"Health check ({kind}) raised {type(e).__name__}: {e}")
        status = getattr(state, "check_status", None)
        if isinstance(status, dict):
            status["lastError"] = type(e).__name__


async def run_scheduler(state, *, clock: Callable[[], datetime] = _utc_now, sleep=asyncio.sleep) -> None:
    """
    Forever: wait for the next slot through wallclock (sleeps of ≤ 60 s, the
    clock re-read after each; 3.4 follow-up), then run the slot if it is due
    and at most GRACE_SECONDS late. Checks are awaited in sequence, so two
    never overlap. A late slot (a check overran it, the laptop slept, a
    restart) is skipped with a WARNING naming how many were missed, never
    caught up; so are the slots a wake between slots passed. A loop that has
    handled no slot yet reports only the one it lands in (the restart rule).
    After a check the clock is re-read before waiting, so a check that ran
    into the next slot's grace window still runs it. A host pause the harness
    saw is kept for the next check's payload (addition 1). Cancellation
    (shutdown) propagates.
    """
    logger.info("Regime scheduler running")
    last_start: Optional[datetime] = None
    while True:
        try:
            now = clock()
            due = slot_for(now)
            if due is not None and due[1] != last_start:
                kind, start = due
                late = (now - start).total_seconds()
                missed = _missed_between(last_start, start) + (1 if late > GRACE_SECONDS else 0)
                last_start = start
                if missed:
                    logger.warning(
                        f"Missed {missed} health check slot(s) up to {start.isoformat()} "
                        f"(woke {late:.0f}s after that slot)"
                    )
                if late <= GRACE_SECONDS:
                    await _guarded_check(state, kind, clock)
                    continue
            elif last_start is not None:
                passed = last_slot_before(now)
                if passed is not None and passed[1] > last_start:
                    start = passed[1]
                    logger.warning(
                        f"Missed {_missed_between(last_start, start) + 1} health check slot(s) up to "
                        f"{start.isoformat()} (woke {(now - start).total_seconds():.0f}s after that slot)"
                    )
                    last_start = start
            nxt = next_slot_after(clock())
            target = nxt[1] if nxt is not None else clock() + timedelta(seconds=FALLBACK_SLEEP_SECONDS)
        except Exception as e:
            logger.error(f"Scheduler loop error {type(e).__name__}: {e}")
            target = clock() + timedelta(seconds=FALLBACK_SLEEP_SECONDS)
        wake = await wallclock.sleep_until(target, clock=clock, sleep=sleep, log=logger)
        if wake.paused_seconds:
            state.pending_paused_seconds = (getattr(state, "pending_paused_seconds", None) or 0) + wake.paused_seconds
