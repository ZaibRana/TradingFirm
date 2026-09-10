"""Part 3.4 — when a health check runs (spec decision 3). The real XNYS
calendar (bundled rules, no network) under frozen time: every `now` is
passed in, nothing reads the clock."""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pytest

import scheduler

ET = ZoneInfo("America/New_York")


def et(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET)


@pytest.fixture(autouse=True)
def fresh_calendar():
    scheduler._calendar_state["cal"] = None
    yield
    scheduler._calendar_state["cal"] = None


def _counting_factory(monkeypatch, factory=None):
    calls = []
    real = factory or scheduler._build_calendar

    def build(day):
        calls.append(day)
        return real(day)

    monkeypatch.setattr(scheduler, "_build_calendar", build)
    return calls


def test_slot_market_every_five_minutes_inclusive_of_close():
    d = (2026, 9, 10)    # Thursday, regular session
    assert scheduler.slot_for(et(*d, 9, 29, 59)) is None
    assert scheduler.slot_for(et(*d, 9, 30)) == ("market", et(*d, 9, 30))
    assert scheduler.slot_for(et(*d, 9, 35)) == ("market", et(*d, 9, 35))
    assert scheduler.slot_for(et(*d, 9, 37, 30)) == ("market", et(*d, 9, 35))
    assert scheduler.slot_for(et(*d, 16, 0)) == ("market", et(*d, 16, 0))
    assert scheduler.slot_for(et(*d, 16, 5)) is None


def test_slot_settle_1620_on_session_days_only():
    assert scheduler.slot_for(et(2026, 9, 10, 16, 20)) == ("settle", et(2026, 9, 10, 16, 20))
    assert scheduler.slot_for(et(2026, 9, 10, 16, 19, 59)) is None
    assert scheduler.slot_for(et(2026, 9, 7, 16, 20)) is None     # Labor Day
    assert scheduler.slot_for(et(2026, 9, 12, 16, 20)) is None    # Saturday


def test_slot_early_close_uses_calendar_close():
    d = (2026, 11, 27)   # day after Thanksgiving, closes 13:00
    assert scheduler.slot_for(et(*d, 13, 0)) == ("market", et(*d, 13, 0))
    assert scheduler.slot_for(et(*d, 13, 5)) is None
    assert scheduler.slot_for(et(*d, 15, 0)) is None
    assert scheduler.slot_for(et(*d, 16, 20)) == ("settle", et(*d, 16, 20))


@pytest.mark.parametrize("day", [date(2026, 9, 7), date(2026, 11, 26), date(2026, 9, 12), date(2026, 9, 13)])
def test_slot_holiday_and_weekend_have_none(day):
    assert scheduler.slots_for_day(day) == []
    start = datetime(day.year, day.month, day.day, tzinfo=ET)
    for minutes in range(0, 24 * 60, 5):
        assert scheduler.slot_for(start + timedelta(minutes=minutes)) is None


def test_slot_counts_per_session_day():
    normal = scheduler.slots_for_day(date(2026, 9, 10))
    assert [k for k, _ in normal].count("market") == 79
    assert [k for k, _ in normal].count("settle") == 1
    assert normal[0] == ("market", et(2026, 9, 10, 9, 30))
    assert normal[-2] == ("market", et(2026, 9, 10, 16, 0))
    assert normal[-1] == ("settle", et(2026, 9, 10, 16, 20))

    early = scheduler.slots_for_day(date(2026, 11, 27))
    assert [k for k, _ in early].count("market") == 43
    assert [k for k, _ in early].count("settle") == 1
    starts = [s for _, s in normal]
    assert starts == sorted(starts)


def test_slot_uses_eastern_time_across_dst():
    # 2026-11-01 DST ends: Monday 11-02 09:30 EST is 14:30 UTC.
    utc = timezone.utc
    assert scheduler.slot_for(datetime(2026, 11, 2, 14, 30, tzinfo=utc)) == (
        "market", datetime(2026, 11, 2, 14, 30, tzinfo=utc))
    assert scheduler.slot_for(datetime(2026, 11, 2, 13, 30, tzinfo=utc)) is None
    # The Friday before, still EDT: 09:30 is 13:30 UTC.
    assert scheduler.slot_for(datetime(2026, 10, 30, 13, 30, tzinfo=utc)) == (
        "market", datetime(2026, 10, 30, 13, 30, tzinfo=utc))
    # Settle follows the Eastern wall clock too.
    assert scheduler.slot_for(datetime(2026, 11, 2, 21, 20, tzinfo=utc))[0] == "settle"
    assert scheduler.slot_for(datetime(2026, 10, 30, 20, 20, tzinfo=utc))[0] == "settle"


def test_next_slot_after_skips_weekend_and_holiday():
    assert scheduler.next_slot_after(et(2026, 9, 10, 9, 31, 10)) == ("market", et(2026, 9, 10, 9, 35))
    # Strictly after: from a slot start, the next one.
    assert scheduler.next_slot_after(et(2026, 9, 10, 9, 35)) == ("market", et(2026, 9, 10, 9, 40))
    assert scheduler.next_slot_after(et(2026, 9, 10, 16, 0)) == ("settle", et(2026, 9, 10, 16, 20))
    # Friday after settle → Monday open.
    assert scheduler.next_slot_after(et(2026, 9, 11, 16, 21)) == ("market", et(2026, 9, 14, 9, 30))
    # Friday before Labor Day → Tuesday open.
    assert scheduler.next_slot_after(et(2026, 9, 4, 16, 21)) == ("market", et(2026, 9, 8, 9, 30))
    # Early close: after 13:00 the next slot is that day's settle.
    assert scheduler.next_slot_after(et(2026, 11, 27, 13, 1)) == ("settle", et(2026, 11, 27, 16, 20))


@pytest.mark.parametrize("bad", [datetime(2026, 9, 10, 14, 0), "2026-09-10T14:00:00+00:00", None])
def test_slot_rejects_naive_datetime(bad):
    with pytest.raises(ValueError):
        scheduler.slot_for(bad)
    with pytest.raises(ValueError):
        scheduler.next_slot_after(bad)


def test_calendar_out_of_bounds_rebuilds_then_fails_closed(monkeypatch, caplog):
    # A cached calendar that no longer covers the date is rebuilt once, and
    # the rebuild serves the slot.
    scheduler._calendar_state["cal"] = xcals.get_calendar("XNYS", start="2026-01-02", end="2026-03-31")
    calls = _counting_factory(monkeypatch)
    with caplog.at_level(logging.WARNING):
        assert scheduler.slot_for(et(2026, 9, 10, 9, 30)) == ("market", et(2026, 9, 10, 9, 30))
    assert len(calls) == 1
    assert any("rebuilding" in r.getMessage() for r in caplog.records)

    # A rebuild that still cannot cover it: no slot, ERROR, no second rebuild.
    scheduler._calendar_state["cal"] = None
    narrow = lambda day: xcals.get_calendar("XNYS", start="2026-01-02", end="2026-03-31")
    calls = _counting_factory(monkeypatch, narrow)
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        assert scheduler.slot_for(et(2026, 9, 10, 9, 30)) is None
        assert scheduler.next_slot_after(et(2026, 9, 10, 9, 30)) is None
    assert len(calls) == 2    # exactly one rebuild per call
    assert sum(r.levelno == logging.ERROR for r in caplog.records) == 2


def test_slot_for_is_pure_and_calendar_cached(monkeypatch):
    calls = _counting_factory(monkeypatch)
    now = et(2026, 9, 10, 11, 2, 30)
    first = scheduler.slot_for(now)
    assert scheduler.slot_for(now) == first == ("market", et(2026, 9, 10, 11, 0))
    assert scheduler.next_slot_after(now) == scheduler.next_slot_after(now)
    assert len(calls) == 1
