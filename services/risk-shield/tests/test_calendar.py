"""Part 3.5 — econ_calendar.py, the hand-maintained calendar file. No
network, no Postgres, no Redis: the loader reads one JSON file."""

import copy
import json
import logging
from datetime import date

import pytest

import econ_calendar
from econ_calendar import CalendarUnavailable

# Spec 3.5 decision 8's table, copied (docs/ is not mounted in the twin).
SPEC_TABLE = [
    ("2026-07-02", "08:30", "jobs", "June 2026"),
    ("2026-07-14", "08:30", "cpi", "June 2026"),
    ("2026-07-29", "14:00", "fomc", "meeting Jul 28–29"),
    ("2026-08-07", "08:30", "jobs", "July 2026"),
    ("2026-08-12", "08:30", "cpi", "July 2026"),
    ("2026-09-04", "08:30", "jobs", "August 2026"),
    ("2026-09-11", "08:30", "cpi", "August 2026"),
    ("2026-09-16", "14:00", "fomc", "meeting Sep 15–16, with projections"),
    ("2026-10-02", "08:30", "jobs", "September 2026"),
    ("2026-10-14", "08:30", "cpi", "September 2026"),
    ("2026-10-28", "14:00", "fomc", "meeting Oct 27–28"),
    ("2026-11-06", "08:30", "jobs", "October 2026"),
    ("2026-11-10", "08:30", "cpi", "October 2026"),
    ("2026-12-04", "08:30", "jobs", "November 2026"),
    ("2026-12-09", "14:00", "fomc", "meeting Dec 8–9, with projections"),
    ("2026-12-10", "08:30", "cpi", "November 2026"),
]
SOURCES = {
    "fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "cpi": "https://www.bls.gov/schedule/news_release/cpi.htm",
    "jobs": "https://www.bls.gov/schedule/news_release/empsit.htm",
}
VALID = {
    "coversFrom": "2026-07-01",
    "coversThrough": "2026-12-31",
    "timezone": "America/New_York",
    "retrieved": "2026-09-10",
    "sources": SOURCES,
    "events": [
        {"date": "2026-09-16", "time": "14:00", "type": "fomc", "title": "FOMC rate decision", "detail": "d"},
        {"date": "2026-09-11", "time": "08:30", "type": "cpi", "title": "Consumer Price Index", "detail": "d"},
    ],
}
TODAY = date(2026, 9, 10)


@pytest.fixture(autouse=True)
def fresh_cache():
    econ_calendar._cache.clear()
    yield
    econ_calendar._cache.clear()


def _write(tmp_path, body, name="cal.json"):
    path = tmp_path / name
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return path


def _mutated(change):
    body = copy.deepcopy(VALID)
    change(body)
    return body


def test_shipped_calendar_matches_spec_table():
    calendar = econ_calendar.load(today=TODAY)
    assert calendar["coversFrom"] == date(2026, 7, 1)
    assert calendar["coversThrough"] == date(2026, 12, 31)
    assert calendar["retrieved"] == date(2026, 9, 10)
    assert calendar["sources"] == SOURCES
    rows = [(e["date"].isoformat(), e["time"], e["type"], e["detail"]) for e in calendar["events"]]
    assert rows == SPEC_TABLE
    titles = {e["type"]: e["title"] for e in calendar["events"]}
    assert titles == {"jobs": "Employment Situation", "cpi": "Consumer Price Index",
                      "fomc": "FOMC rate decision"}


BAD = [
    ("not-json", "{nope", "not valid JSON"),
    ("not-an-object", [], "calendar: expected an object"),
    ("missing-top-key", _mutated(lambda b: b.pop("coversThrough")), "missing ['coversThrough']"),
    ("extra-top-key", _mutated(lambda b: b.update(note="x")), "unexpected ['note']"),
    ("timezone", _mutated(lambda b: b.update(timezone="UTC")), "timezone"),
    ("covers-inverted", _mutated(lambda b: b.update(coversFrom="2027-01-01")), "coversFrom: after"),
    ("sources-missing-cpi", _mutated(lambda b: b["sources"].pop("cpi")), "sources: missing ['cpi']"),
    ("sources-http", _mutated(lambda b: b["sources"].update(cpi="http://bls.gov")), "sources.cpi"),
    ("events-empty", _mutated(lambda b: b.update(events=[])), "events: expected a non-empty list"),
    ("bad-date", _mutated(lambda b: b["events"][0].update(date="2026-13-01")), "events[0].date: not a real date"),
    ("compact-date", _mutated(lambda b: b["events"][0].update(date="20260916")), "events[0].date: expected YYYY-MM-DD"),
    ("bad-time", _mutated(lambda b: b["events"][0].update(time="8:30")), "events[0].time"),
    ("time-24", _mutated(lambda b: b["events"][0].update(time="24:00")), "events[0].time"),
    ("unknown-type", _mutated(lambda b: b["events"][0].update(type="gdp")), "events[0].type"),
    ("blank-title", _mutated(lambda b: b["events"][0].update(title="  ")), "events[0].title"),
    ("missing-detail", _mutated(lambda b: b["events"][0].pop("detail")), "events[0]: missing ['detail']"),
    ("outside-coverage", _mutated(lambda b: b["events"][0].update(date="2027-01-05")), "outside coverage"),
    ("duplicate", _mutated(lambda b: b["events"].append(dict(b["events"][0], time="15:00"))), "duplicate fomc on 2026-09-16"),
]


@pytest.mark.parametrize("body, fragment", [(b, f) for _, b, f in BAD], ids=[i for i, _, _ in BAD])
def test_calendar_validation_rejects_bad_events(tmp_path, caplog, body, fragment):
    caplog.set_level(logging.ERROR, logger="econ_calendar")
    path = _write(tmp_path, body)
    with pytest.raises(CalendarUnavailable) as exc:
        econ_calendar.load(path, today=TODAY)
    assert fragment in str(exc.value)
    assert "Econ calendar unavailable" in caplog.text
    assert str(path) not in econ_calendar._cache


def test_calendar_missing_file_raises(tmp_path):
    with pytest.raises(CalendarUnavailable, match="cannot read"):
        econ_calendar.load(tmp_path / "absent.json", today=TODAY)


def test_calendar_loaded_once_per_process(tmp_path):
    path = _write(tmp_path, VALID)
    first = econ_calendar.load(path, today=TODAY)
    assert [e["date"] for e in first["events"]] == [date(2026, 9, 11), date(2026, 9, 16)]  # sorted

    path.write_text("{garbage", encoding="utf-8")        # never re-read once valid
    assert econ_calendar.load(path, today=TODAY) is first

    broken = _write(tmp_path, "{garbage", name="broken.json")
    with pytest.raises(CalendarUnavailable):
        econ_calendar.load(broken, today=TODAY)
    broken.write_text(json.dumps(VALID), encoding="utf-8")   # a failure is not cached
    assert econ_calendar.load(broken, today=TODAY)["coversThrough"] == date(2026, 12, 31)


def test_calendar_renewal_warning_within_14_days(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="econ_calendar")
    path = _write(tmp_path, VALID)

    calendar = econ_calendar.load(path, today=date(2026, 12, 17))    # 14 days left
    assert "renew by" not in caplog.text
    assert econ_calendar.coverage_short(calendar, date(2026, 12, 17)) is False

    econ_calendar._cache.clear()
    calendar = econ_calendar.load(path, today=date(2026, 12, 18))    # 13 days left
    warning = caplog.text
    assert "covers through 2026-12-31: renew by 2026-12-17" in warning
    assert all(url in warning for url in SOURCES.values())
    assert econ_calendar.coverage_short(calendar, date(2026, 12, 18)) is True

    # Recomputed against a later day, no reload: a process loaded in
    # September still reads short in December.
    econ_calendar._cache.clear()
    caplog.clear()
    calendar = econ_calendar.load(path, today=TODAY)
    assert "renew by" not in caplog.text
    assert econ_calendar.coverage_short(calendar, date(2026, 12, 18)) is True
