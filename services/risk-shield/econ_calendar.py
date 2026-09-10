"""
TradingFirm — the hand-maintained economic calendar (Part 3.5 decision 8).

data/econ_calendar.json lists FOMC decisions, CPI releases and jobs reports
for a stated coverage window, copied by hand from the Fed and BLS pages. It
is the only calendar source: nothing here calls an API (Finnhub's
/calendar/economic is likely premium, plan §2).

load() validates the file once per process and caches it only when it is
valid. A missing or invalid file raises CalendarUnavailable (logged at ERROR)
every time it is asked for, so a fixed file is picked up without a restart.

Renewal: the file is short when coversThrough − today (ET) < 14 days. load()
warns about that once, at load. coverage_short(calendar, today) recomputes
it for /health and the news poller, because a long-lived process loaded the
file weeks before it runs short.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CALENDAR_PATH = Path(__file__).parent / "data" / "econ_calendar.json"
TIMEZONE = "America/New_York"
EVENT_TYPES = ("fomc", "cpi", "jobs")
RENEWAL_DAYS = 14

_TOP_KEYS = {"coversFrom", "coversThrough", "timezone", "retrieved", "sources", "events"}
_EVENT_KEYS = {"date", "time", "type", "title", "detail"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CalendarUnavailable(RuntimeError):
    """The calendar file is missing, unreadable or fails validation."""


# Valid calendars only, keyed by path. A failure is never cached.
_cache: dict[str, dict] = {}


def et_today(now: Optional[datetime] = None) -> date:
    return (now or datetime.now(ET)).astimezone(ET).date()


def _date(value: Any, where: str) -> date:
    if not isinstance(value, str) or not _DATE.match(value):
        raise CalendarUnavailable(f"{where}: expected YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CalendarUnavailable(f"{where}: not a real date: {value!r}") from None


def _keys(obj: Any, expected: set, where: str) -> None:
    if not isinstance(obj, dict):
        raise CalendarUnavailable(f"{where}: expected an object")
    missing, extra = expected - obj.keys(), obj.keys() - expected
    if missing or extra:
        raise CalendarUnavailable(f"{where}: missing {sorted(missing)}, unexpected {sorted(extra)}")


def validate(raw: Any) -> dict:
    """The parsed file → a calendar with real dates and events sorted by
    (date, time, type). Raises CalendarUnavailable naming the problem."""
    _keys(raw, _TOP_KEYS, "calendar")
    if raw["timezone"] != TIMEZONE:
        raise CalendarUnavailable(f"timezone: only {TIMEZONE} is supported, got {raw['timezone']!r}")
    covers_from = _date(raw["coversFrom"], "coversFrom")
    covers_through = _date(raw["coversThrough"], "coversThrough")
    if covers_from > covers_through:
        raise CalendarUnavailable("coversFrom: after coversThrough")
    retrieved = _date(raw["retrieved"], "retrieved")

    sources = raw["sources"]
    _keys(sources, set(EVENT_TYPES), "sources")
    for kind, url in sources.items():
        if not isinstance(url, str) or not url.startswith("https://"):
            raise CalendarUnavailable(f"sources.{kind}: expected an https URL")

    if not isinstance(raw["events"], list) or not raw["events"]:
        raise CalendarUnavailable("events: expected a non-empty list")
    events, seen = [], set()
    for i, item in enumerate(raw["events"]):
        where = f"events[{i}]"
        _keys(item, _EVENT_KEYS, where)
        day = _date(item["date"], f"{where}.date")
        if not isinstance(item["time"], str) or not _TIME.match(item["time"]):
            raise CalendarUnavailable(f"{where}.time: expected HH:MM, got {item['time']!r}")
        if item["type"] not in EVENT_TYPES:
            raise CalendarUnavailable(f"{where}.type: expected one of {EVENT_TYPES}, got {item['type']!r}")
        if not isinstance(item["title"], str) or not item["title"].strip():
            raise CalendarUnavailable(f"{where}.title: expected a non-blank string")
        if not isinstance(item["detail"], str):
            raise CalendarUnavailable(f"{where}.detail: expected a string")
        if not covers_from <= day <= covers_through:
            raise CalendarUnavailable(f"{where}.date: {day} is outside coverage {covers_from}…{covers_through}")
        if (day, item["type"]) in seen:
            raise CalendarUnavailable(f"{where}: duplicate {item['type']} on {day}")
        seen.add((day, item["type"]))
        events.append({"date": day, "time": item["time"], "type": item["type"],
                       "title": item["title"].strip(), "detail": item["detail"]})

    events.sort(key=lambda e: (e["date"], e["time"], e["type"]))
    return {"coversFrom": covers_from, "coversThrough": covers_through, "retrieved": retrieved,
            "sources": dict(sources), "events": events}


def coverage_short(calendar: dict, today: date) -> bool:
    """True when fewer than RENEWAL_DAYS days of coverage remain."""
    return (calendar["coversThrough"] - today).days < RENEWAL_DAYS


def renewal_message(calendar: dict) -> str:
    renew_by = calendar["coversThrough"] - timedelta(days=RENEWAL_DAYS)
    urls = ", ".join(calendar["sources"][kind] for kind in EVENT_TYPES)
    return (f"Econ calendar covers through {calendar['coversThrough']}: renew by {renew_by} "
            f"from {urls} (CLAUDE.md, calendar renewal)")


def load(path: Optional[Path] = None, *, today: Optional[date] = None) -> dict:
    """The validated calendar at `path` (default data/econ_calendar.json),
    cached per process once valid. Raises CalendarUnavailable (ERROR)."""
    path = Path(path or CALENDAR_PATH)
    cached = _cache.get(str(path))
    if cached is not None:
        return cached
    try:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise CalendarUnavailable(f"{path.name}: cannot read ({type(e).__name__})") from None
        try:
            raw = json.loads(text)
        except ValueError:
            raise CalendarUnavailable(f"{path.name}: not valid JSON") from None
        calendar = validate(raw)
    except CalendarUnavailable as e:
        logger.error(f"Econ calendar unavailable: {e}")
        raise

    _cache[str(path)] = calendar
    logger.info(
        f"Econ calendar loaded: {len(calendar['events'])} events, "
        f"{calendar['coversFrom']} … {calendar['coversThrough']}"
    )
    if coverage_short(calendar, today or et_today()):
        logger.warning(renewal_message(calendar))
    return calendar
