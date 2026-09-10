"""
TradingFirm — the regime scheduler (Part 3.4).

When a health check runs (spec 3.4 decision 3), from the XNYS calendar:
  market  every 5-minute slot of the regular session, open … close inclusive
          (09:30 … 16:00 = 79 slots; 43 on an early close)
  settle  16:20 ET on every session day, early closes included. That is after
          3.3's 16:15 partial-bar cut-off, so the day's bars are complete and
          3.3's rule stays unchanged.
Nothing runs outside those slots (night mode is 3.4b).

Every function takes an aware `now`; nothing here reads the clock.
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

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
