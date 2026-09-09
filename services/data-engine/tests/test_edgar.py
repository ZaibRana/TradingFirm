"""
TradingFirm — SEC EDGAR client + filings fetcher tests (Part 2.2).
Spec and failure-branch table: docs/specs/2.2.md.

Zero network: every HTTP call is intercepted by respx with
`assert_all_mocked=True`, so a stray real request fails the test. Bodies
come from the recorded fixtures in tests/fixtures/edgar/ (written once by
tests/record_edgar_live.py, trimmed to 5 tickers / 60 filings) plus small
hand-built submissions bodies for the boundary logic. Redis is the
in-process FakeRedis; the DB pool is the mocked asyncpg pool the store
tests use. The limiter runs on a fake clock — no real sleeps anywhere.
"""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from cache import EDGAR_CIK_MAP_KEY, edgar_key
from db import upsert_filings
from providers.context import edgar
from providers.context.edgar_client import (
    EDGAR_ARCHIVES_BASE,
    EDGAR_TICKERS_URL,
    EdgarClient,
    EdgarError,
    EdgarNotConfigured,
    EdgarNotFound,
    EdgarRateLimited,
    submissions_url,
)
from providers.context.ratelimit import RateLimiter, edgar_limiter
from tests.fake_redis import FakeRedis

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"
_HERE = Path(__file__).resolve()
MIGRATION = next(
    (p for p in (
        Path("/migrations/004_filings.sql"),
        _HERE.parents[3] / "infra" / "supabase" / "migrations" / "004_filings.sql" if len(_HERE.parents) > 3 else Path("/nonexistent"),
    ) if p.exists()),
    Path("/migrations/004_filings.sql"),
)
TODAY = date(2026, 9, 9)
SINCE_30 = date(2026, 8, 10)
SINCE_90 = date(2026, 6, 11)
AAPL_CIK = 320193
SUBMISSIONS_RE = r"https://data\.sec\.gov/submissions/CIK\d{10}\.json"
UA = "TradingFirm test@example.com"


def _fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _fixture_dates() -> list[date]:
    return [date.fromisoformat(d) for d in _fixture("AAPL_submissions")["filings"]["recent"]["filingDate"]]


def _body(rows: list[tuple]) -> dict:
    """Hand-built submissions body. Each row: (accession, filingDate, form,
    primaryDocument, items, acceptanceDateTime)."""
    cols = {
        "accessionNumber": [r[0] for r in rows],
        "filingDate": [r[1] for r in rows],
        "reportDate": [r[1] for r in rows],
        "acceptanceDateTime": [r[5] for r in rows],
        "form": [r[2] for r in rows],
        "primaryDocument": [r[3] for r in rows],
        "primaryDocDescription": ["" for _ in rows],
        "items": [r[4] for r in rows],
    }
    return {"cik": str(AAPL_CIK), "filings": {"recent": cols, "files": []}}


class _FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _test_limiter(fake: _FakeClock) -> RateLimiter:
    return RateLimiter(max_calls=10, window=1.0, min_gap=0.0, clock=fake.clock, sleep=fake.sleep)


def _client(ua: str = UA, **kw) -> EdgarClient:
    limiter = kw.pop("limiter", _test_limiter(_FakeClock()))
    return EdgarClient(ua, limiter=limiter, **kw)


def _make_pool():
    conn = AsyncMock()
    conn.executemany = AsyncMock()
    pool = MagicMock()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool, conn


@pytest.fixture
def mock_api():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as api:
        yield api


def _mock_edgar(mock_api, submissions=None):
    tickers = mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(200, json=_fixture("company_tickers")))
    body = submissions if submissions is not None else _fixture("AAPL_submissions")
    subs = mock_api.get(url__regex=SUBMISSIONS_RE).mock(return_value=httpx.Response(200, json=body))
    return tickers, subs


# ── Client: failure branches ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_not_configured_raises(mock_api):
    client = _client(ua="")
    with pytest.raises(EdgarNotConfigured):
        await client.get_json(EDGAR_TICKERS_URL)
    assert not mock_api.calls  # no HTTP at all
    await client.aclose()


@pytest.mark.asyncio
async def test_client_sends_user_agent_header(mock_api):
    route = mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(200, json={}))
    client = _client()
    await client.get_json(EDGAR_TICKERS_URL)
    request = route.calls.last.request
    assert request.headers["User-Agent"] == UA
    assert request.headers["Accept"] == "application/json"
    assert "example.com" not in str(request.url)
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 429])
async def test_client_blocked_raises_rate_limited(mock_api, status):
    route = mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(status, text="Request Rate Threshold Exceeded"))
    client = _client()
    with pytest.raises(EdgarRateLimited):
        await client.get_json(EDGAR_TICKERS_URL)
    assert route.call_count == 1  # exactly one attempt, no retry
    await client.aclose()


@pytest.mark.asyncio
async def test_client_404_raises_not_found(mock_api):
    mock_api.get(url__regex=SUBMISSIONS_RE).mock(return_value=httpx.Response(404, text="no"))
    client = _client()
    with pytest.raises(EdgarNotFound):
        await client.get_json(submissions_url(1))
    await client.aclose()


@pytest.mark.asyncio
async def test_client_server_error(mock_api):
    mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(503, text="down"))
    client = _client()
    with pytest.raises(EdgarError) as exc_info:
        await client.get_json(EDGAR_TICKERS_URL)
    assert type(exc_info.value) is EdgarError
    await client.aclose()


@pytest.mark.asyncio
async def test_client_timeout(mock_api):
    mock_api.get(EDGAR_TICKERS_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    client = _client()
    with pytest.raises(EdgarError) as exc_info:
        await client.get_json(EDGAR_TICKERS_URL)
    assert type(exc_info.value) is EdgarError
    await client.aclose()


@pytest.mark.asyncio
async def test_client_bad_json(mock_api):
    mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    client = _client()
    with pytest.raises(EdgarError):
        await client.get_json(EDGAR_TICKERS_URL)
    await client.aclose()


@pytest.mark.asyncio
async def test_limiter_caps_edgar_rate():
    """One mechanism: 10 per rolling second, no gap. The module-level
    instance is the client default, shared by every client in the process."""
    assert (edgar_limiter.max_calls, edgar_limiter.window, edgar_limiter.min_gap) == (10, 1.0, 0.0)
    assert EdgarClient(UA).limiter is edgar_limiter
    assert EdgarClient(UA).limiter is EdgarClient(UA).limiter
    fake = _FakeClock()
    limiter = _test_limiter(fake)
    start = fake.now
    for _ in range(10):
        assert await limiter.acquire() == 0.0  # ten at once are free
    assert await limiter.acquire() == pytest.approx(1.0)  # the 11th waits for the window
    assert fake.now - start == pytest.approx(1.0)
    assert await limiter.acquire() == pytest.approx(0.0)  # window has slid: the 12th is free


def test_submissions_url_zero_pads_cik():
    assert submissions_url(320193) == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert submissions_url("1") == "https://data.sec.gov/submissions/CIK0000000001.json"


# ── Pure helpers ─────────────────────────────────────────────────────────


def test_filing_url_with_and_without_primary_doc():
    acc = "0000320193-26-000018"
    assert edgar.filing_url(AAPL_CIK, acc, "aapl-20260730.htm") == (
        f"{EDGAR_ARCHIVES_BASE}/320193/000032019326000018/aapl-20260730.htm"
    )
    assert edgar.filing_url(AAPL_CIK, acc, "xslF345X06/form4.xml").endswith("/000032019326000018/xslF345X06/form4.xml")
    assert edgar.filing_url(AAPL_CIK, acc, "") == (
        f"{EDGAR_ARCHIVES_BASE}/320193/000032019326000018/{acc}-index.htm"
    )
    assert edgar.filing_url(AAPL_CIK, acc, None).endswith(f"/{acc}-index.htm")


@pytest.mark.parametrize("form,base", [("8-K", "8-K"), ("8-K/A", "8-K"), (" 4 ", "4"), ("4/A", "4"), ("SC 13G/A", "SC 13G"), ("", "")])
def test_base_form_folds_amendments(form, base):
    assert edgar.base_form(form) == base


def test_parse_cik_map_fixture():
    mapping = edgar.parse_cik_map(_fixture("company_tickers"))
    assert mapping["AAPL"] == AAPL_CIK
    assert mapping["GOOG"] == mapping["GOOGL"] == 1652044  # two tickers, one CIK
    assert mapping["BRK-B"] == 1067983  # a key, but unreachable through cik_for (letters-only validator)
    assert all(k == k.upper() for k in mapping)


def test_parse_cik_map_skips_bad_entries():
    body = {
        "0": {"cik_str": 1, "ticker": "aapl"},
        "1": {"cik_str": "x", "ticker": "BAD"},
        "2": {"cik_str": 3, "ticker": ""},
        "3": "junk",
        "4": {"cik_str": "5", "ticker": " msft "},
    }
    assert edgar.parse_cik_map(body) == {"AAPL": 1, "MSFT": 5}
    assert edgar.parse_cik_map([]) == {}
    assert edgar.parse_cik_map(None) == {}


def test_parse_submissions_fixture_shape():
    rows, oldest = edgar.parse_submissions("aapl", AAPL_CIK, _fixture("AAPL_submissions"), today=TODAY, max_days=365)
    assert len(rows) == 60 and oldest == min(_fixture_dates()) == date(2025, 11, 14)
    assert set(rows[0]) == {"ticker", "form", "filed_on", "accepted_at", "accession", "url", "meta"}
    assert set(rows[0]["meta"]) == {"cik", "reportDate", "primaryDocument", "primaryDocDescription", "items"}
    assert all(f["ticker"] == "AAPL" and f["meta"]["cik"] == AAPL_CIK for f in rows)
    assert all(f["url"].startswith(f"{EDGAR_ARCHIVES_BASE}/320193/") for f in rows)
    assert [f["filed_on"] for f in rows] == sorted((f["filed_on"] for f in rows), reverse=True)  # newest first
    earnings = next(f for f in rows if f["accession"] == "0000320193-26-000018")
    assert earnings["form"] == "8-K" and earnings["filed_on"] == "2026-07-30"
    assert earnings["accepted_at"] == "2026-07-30T20:30:28.000Z"
    assert earnings["meta"]["items"] == "2.02,9.01"
    assert earnings["url"] == f"{EDGAR_ARCHIVES_BASE}/320193/000032019326000018/aapl-20260730.htm"


def test_parse_submissions_window_by_date():
    """Rows are kept by filingDate (last max_days), never by count; `oldest`
    is the whole block's oldest date regardless of the window."""
    rows, oldest = edgar.parse_submissions("AAPL", AAPL_CIK, _fixture("AAPL_submissions"), today=TODAY)
    expected = sum(1 for d in _fixture_dates() if d >= SINCE_90)
    assert 0 < len(rows) == expected < 60
    assert all(date.fromisoformat(f["filed_on"]) >= SINCE_90 for f in rows)
    assert oldest == date(2025, 11, 14)


def test_parse_submissions_drops_incomplete_rows(caplog):
    body = _body([
        ("0000000001-26-000001", "2026-09-01", "8-K", "a.htm", "2.02", "2026-09-01T20:30:00.000Z"),
        ("", "2026-09-01", "8-K", "b.htm", None, None),                    # no accession
        ("0000000001-26-000003", "", "4", "c.xml", None, None),            # no filing date
        ("0000000001-26-000004", "2026-09-01", "", "d.htm", None, None),   # no form
        ("0000000001-26-000005", "not-a-date", "4", "e.xml", None, None),  # malformed date
        ("0000000001-26-000006", "2026-08-30", "4", "f.xml", None, None),
    ])
    rows, oldest = edgar.parse_submissions("AAPL", AAPL_CIK, body, today=TODAY)
    assert [f["accession"][-6:] for f in rows] == ["000001", "000006"]
    assert oldest == date(2026, 8, 30)  # dropped rows do not count
    assert "dropped 4" in caplog.text


def test_parse_submissions_unequal_columns_raises():
    """A short array can mean a shifted middle, not a short tail: every row
    after the shift would pair the wrong accession with the wrong form.
    Closed: EdgarError, and through recent_filings nothing is cached."""
    body = _body([
        ("0000000001-26-000001", "2026-09-01", "8-K", "a.htm", "2.02", None),
        ("0000000001-26-000002", "2026-08-30", "4", "b.xml", None, None),
    ])
    body["filings"]["recent"]["form"] = ["8-K"]  # one short
    with pytest.raises(EdgarError):
        edgar.parse_submissions("AAPL", AAPL_CIK, body, today=TODAY)


@pytest.mark.asyncio
async def test_recent_filings_unequal_columns_not_cached(mock_api):
    body = _body([("0000000001-26-000001", "2026-09-01", "8-K", "a.htm", "2.02", None)])
    body["filings"]["recent"]["filingDate"] = []
    _mock_edgar(mock_api, submissions=body)
    redis = FakeRedis()
    client = _client()
    with pytest.raises(EdgarError):
        await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY)
    assert redis.keys() == [EDGAR_CIK_MAP_KEY]  # the map cached, the filings key never written
    await client.aclose()


def test_parse_submissions_absent_column_reads_as_none():
    body = _body([("0000000001-26-000001", "2026-09-01", "8-K", "a.htm", "2.02", "2026-09-01T20:30:00.000Z")])
    del body["filings"]["recent"]["items"]
    del body["filings"]["recent"]["acceptanceDateTime"]
    rows, _ = edgar.parse_submissions("AAPL", AAPL_CIK, body, today=TODAY)
    assert rows[0]["meta"]["items"] is None and rows[0]["accepted_at"] is None


@pytest.mark.parametrize("body", [{}, {"filings": {}}, {"filings": {"recent": {}}}, {"filings": {"recent": {"accessionNumber": []}}}, [], None])
def test_parse_submissions_empty(body):
    assert edgar.parse_submissions("AAPL", AAPL_CIK, body, today=TODAY) == ([], None)


def test_filter_filings_forms_and_days_boundary():
    body = _body([
        ("0000000001-26-000001", "2026-09-07", "8-K", "a.htm", "2.02", None),   # in
        ("0000000001-26-000002", "2026-09-04", "8-K/A", "b.htm", "5.02", None), # in (amendment)
        ("0000000001-26-000003", "2026-09-07", "10-Q", "c.htm", None, None),    # wrong form
        ("0000000001-26-000004", "2026-08-10", "4", "d.xml", None, None),       # exactly 30 days: in
        ("0000000001-26-000005", "2026-08-09", "4", "e.xml", None, None),       # 31 days: out
        ("0000000001-26-000006", "2026-09-08", "144", "f.htm", None, None),     # wrong form
    ])
    rows, _ = edgar.parse_submissions("AAPL", AAPL_CIK, body, today=TODAY)
    out = edgar.filter_filings(rows, ("8-K", "4"), 30, TODAY)
    assert [f["accession"][-6:] for f in out] == ["000001", "000002", "000004"]
    assert edgar.filter_filings(rows, ("10-Q",), 30, TODAY)[0]["accession"].endswith("000003")
    assert edgar.filter_filings(rows, ("8-k",), 2, TODAY) == [rows[0]]  # case-insensitive form, tight window
    assert edgar.filter_filings([], ("8-K",), 30, TODAY) == []


def test_filing_records_filed_on_and_accepted_at():
    rows, _ = edgar.parse_submissions("AAPL", AAPL_CIK, _body([
        ("0000000001-26-000001", "2026-09-01", "8-K", "a.htm", "2.02", "2026-09-01T20:30:35.000Z"),
        ("0000000001-26-000002", "2026-08-30", "4", "b.xml", None, None),
        ("0000000001-26-000003", "2026-08-29", "4", "c.xml", None, "garbage"),
    ]), today=TODAY)
    recs = edgar.filing_records(rows)
    assert [set(r) for r in recs] == [{"ticker", "form", "filed_on", "accepted_at", "accession", "url", "meta"}] * 3
    assert recs[0]["filed_on"] == date(2026, 9, 1)
    assert recs[0]["accepted_at"] == datetime(2026, 9, 1, 20, 30, 35, tzinfo=timezone.utc)
    assert recs[1]["filed_on"] == date(2026, 8, 30) and recs[1]["accepted_at"] is None  # never fabricated
    assert recs[2]["accepted_at"] is None  # unparseable → None
    assert recs[0]["meta"]["items"] == "2.02" and recs[0]["accession"] == "0000000001-26-000001"
    assert edgar.filing_records([]) == []


# ── Fetchers: happy path on recorded fixtures ────────────────────────────


@pytest.mark.asyncio
async def test_cik_map_parses_fixture(mock_api):
    _mock_edgar(mock_api)
    client = _client()
    mapping = await edgar.cik_map(client)
    assert mapping["AAPL"] == AAPL_CIK and len(mapping) == 5
    assert await edgar.cik_for(client, "aapl") == AAPL_CIK
    assert client.calls_made == 2  # no redis → fetched twice
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_parses_fixture(mock_api, caplog):
    tickers, subs = _mock_edgar(mock_api)
    client = _client()
    rows, truncated = await edgar.recent_filings(client, "aapl", today=TODAY)  # 8-K + 4, 30 days
    assert truncated is False and "reaches back" not in caplog.text
    assert tickers.call_count == 1 and subs.call_count == 1
    assert subs.calls.last.request.url == submissions_url(AAPL_CIK)
    assert rows and all(edgar.base_form(f["form"]) in {"8-K", "4"} for f in rows)
    assert all(SINCE_30 <= date.fromisoformat(f["filed_on"]) <= TODAY for f in rows)
    assert [f["filed_on"] for f in rows] == sorted((f["filed_on"] for f in rows), reverse=True)
    assert "0001140361-26-035325" in {f["accession"] for f in rows}  # the 8-K/A of 2026-09-01
    assert "0000320193-26-000018" not in {f["accession"] for f in rows}  # 2026-07-30 is outside 30 days
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_days_and_forms_params(mock_api):
    _mock_edgar(mock_api)
    client = _client()
    rows, _ = await edgar.recent_filings(client, "AAPL", forms=("8-K",), days=90, today=TODAY)
    assert [f["accession"] for f in rows] == ["0001140361-26-035325", "0000320193-26-000018"]
    assert rows[1]["meta"]["items"] == "2.02,9.01"
    assert rows[1]["url"] == f"{EDGAR_ARCHIVES_BASE}/320193/000032019326000018/aapl-20260730.htm"
    only_tenq, _ = await edgar.recent_filings(client, "AAPL", forms=("10-Q",), days=90, today=TODAY)
    assert [f["form"] for f in only_tenq] == ["10-Q"]
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_window_truncated(mock_api, caplog):
    """Truncated only when the whole recent block's oldest row, before form
    filtering, is newer than today − days; a warning then and never otherwise."""
    short = _body([
        ("0000000001-26-000001", "2026-09-07", "8-K", "a.htm", "2.02", None),
        ("0000000001-26-000002", "2026-09-01", "144", "b.htm", None, None),  # oldest row, a form nobody asked for
    ])
    _mock_edgar(mock_api, submissions=short)
    client = _client()
    rows, truncated = await edgar.recent_filings(client, "AAPL", days=30, today=TODAY)
    assert [f["accession"][-6:] for f in rows] == ["000001"] and truncated is True
    assert "reaches back only to 2026-09-01" in caplog.text
    caplog.clear()
    rows, truncated = await edgar.recent_filings(client, "AAPL", days=8, today=TODAY)  # window starts 09-01: not truncated
    assert truncated is False and "reaches back" not in caplog.text
    await client.aclose()


# ── Fetchers: failure branches ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["AAPL1", "", "TOOLONG", "A.B", "BRK-B"])
async def test_recent_filings_rejects_bad_ticker(mock_api, bad):
    _mock_edgar(mock_api)
    client = _client()
    with pytest.raises(ValueError):
        await edgar.recent_filings(client, bad, today=TODAY)
    with pytest.raises(ValueError):
        await edgar.cik_for(client, bad)
    assert not mock_api.calls
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, -1, 91, 3.5, True, "30"])
async def test_recent_filings_rejects_bad_days(mock_api, days):
    _mock_edgar(mock_api)
    client = _client()
    with pytest.raises(ValueError):
        await edgar.recent_filings(client, "AAPL", days=days, today=TODAY)
    with pytest.raises(ValueError):
        await edgar.recent_filings(client, "AAPL", forms=(), today=TODAY)
    assert not mock_api.calls
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_unknown_ticker_empty(mock_api, caplog):
    tickers, subs = _mock_edgar(mock_api)
    client = _client()
    assert await edgar.recent_filings(client, "ZZZZ", today=TODAY) == ([], False)
    assert await edgar.cik_for(client, "ZZZZ") is None
    assert tickers.call_count == 2 and subs.call_count == 0  # never asks for submissions
    assert "not in company_tickers.json" in caplog.text
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_404_returns_empty(mock_api, caplog):
    """CIK known, submissions 404: the SEC has nothing for it. Open, like
    not-in-map; the client's EdgarNotFound is caught here and cached as
    an empty block for the 15-min TTL."""
    mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(200, json=_fixture("company_tickers")))
    subs = mock_api.get(url__regex=SUBMISSIONS_RE).mock(return_value=httpx.Response(404, text="no"))
    redis = FakeRedis()
    client = _client()
    assert await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY) == ([], False)
    assert "submissions 404" in caplog.text
    assert await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY) == ([], False)
    assert subs.call_count == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_cik_map_empty_body_raises_and_is_not_cached(mock_api):
    mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(200, json={}))
    redis = FakeRedis()
    client = _client()
    with pytest.raises(EdgarError):
        await edgar.cik_map(client, redis=redis)
    assert redis.keys() == []
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_empty_recent_block(mock_api):
    _mock_edgar(mock_api, submissions={"cik": "320193", "filings": {"recent": {}, "files": []}})
    client = _client()
    assert await edgar.recent_filings(client, "AAPL", today=TODAY) == ([], False)
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_blocked_stops(mock_api):
    tickers = mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(403, text="Undeclared Automated Tool"))
    subs = mock_api.get(url__regex=SUBMISSIONS_RE).mock(return_value=httpx.Response(200, json=_fixture("AAPL_submissions")))
    client = _client()
    with pytest.raises(EdgarRateLimited):
        await edgar.recent_filings(client, "AAPL", today=TODAY)
    assert tickers.call_count == 1 and subs.call_count == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_repeat_hits_cache(mock_api):
    tickers, subs = _mock_edgar(mock_api)
    redis = FakeRedis()
    client = _client()
    first = await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY)
    second = await edgar.recent_filings(client, "aapl", redis=redis, today=TODAY)  # case shares the key
    third = await edgar.recent_filings(client, "AAPL", redis=redis, forms=("10-Q",), days=90, today=TODAY)
    assert first == second and third[0] and third != first  # filters run on the cached rows
    assert tickers.call_count == 1 and subs.call_count == 1
    assert sorted(redis.keys()) == sorted([EDGAR_CIK_MAP_KEY, edgar_key("filings", "AAPL")])
    assert 0 < await redis.ttl(EDGAR_CIK_MAP_KEY) <= 86400
    assert 0 < await redis.ttl(edgar_key("filings", "AAPL")) <= 900
    cached = json.loads(await redis.get(edgar_key("filings", "AAPL")))
    assert set(cached) == {"rows", "oldest"} and cached["oldest"] == "2025-11-14"
    assert len(cached["rows"]) == sum(1 for d in _fixture_dates() if d >= SINCE_90)  # 90-day window, every form
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_redis_absent_uncached(mock_api):
    tickers, subs = _mock_edgar(mock_api)
    client = _client()
    await edgar.recent_filings(client, "AAPL", redis=None, today=TODAY)
    await edgar.recent_filings(client, "AAPL", redis=None, today=TODAY)
    assert tickers.call_count == 2 and subs.call_count == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_redis_get_raises_uncached(mock_api):
    tickers, subs = _mock_edgar(mock_api)
    redis = FakeRedis(fail_on={"get"})
    client = _client()
    rows, _ = await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY)
    assert rows
    await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY)
    assert tickers.call_count == 2 and subs.call_count == 2  # every call fetches
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_redis_set_raises(mock_api):
    tickers, subs = _mock_edgar(mock_api)
    redis = FakeRedis(fail_on={"set"})
    client = _client()
    rows, _ = await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY)
    assert rows and redis.keys() == []
    assert tickers.call_count == 1 and subs.call_count == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_corrupt_cik_map_refetches(mock_api):
    tickers, subs = _mock_edgar(mock_api)
    redis = FakeRedis()
    await redis.set(EDGAR_CIK_MAP_KEY, "{not json", ex=100)
    client = _client()
    rows, _ = await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY)
    assert rows and tickers.call_count == 1 and subs.call_count == 1
    assert json.loads(await redis.get(EDGAR_CIK_MAP_KEY))["AAPL"] == AAPL_CIK  # overwritten
    await redis.set(EDGAR_CIK_MAP_KEY, "[]", ex=100)  # valid JSON, wrong shape
    await edgar.cik_map(client, redis=redis)
    assert tickers.call_count == 2 and json.loads(await redis.get(EDGAR_CIK_MAP_KEY))["AAPL"] == AAPL_CIK
    await client.aclose()


@pytest.mark.asyncio
async def test_recent_filings_corrupt_filings_cache_refetches(mock_api):
    tickers, subs = _mock_edgar(mock_api)
    redis = FakeRedis()
    await redis.set(EDGAR_CIK_MAP_KEY, json.dumps({"AAPL": AAPL_CIK}), ex=100)
    await redis.set(edgar_key("filings", "AAPL"), '{"not": "the shape"}', ex=100)  # valid JSON, wrong shape
    client = _client()
    rows, _ = await edgar.recent_filings(client, "AAPL", redis=redis, today=TODAY)
    assert rows and tickers.call_count == 0 and subs.call_count == 1
    assert isinstance(json.loads(await redis.get(edgar_key("filings", "AAPL")))["rows"], list)  # replaced
    await client.aclose()


# ── sync_filings: fetch + store ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_filings_stores_rows(mock_api):
    _mock_edgar(mock_api)
    pool, conn = _make_pool()
    client = _client()
    result = await edgar.sync_filings(client, "AAPL", pool=pool, today=TODAY)
    assert result["stored"] is True and result["ticker"] == "AAPL" and result["truncated"] is False
    assert result["filingsFetched"] == result["filingsSent"] > 0
    assert conn.executemany.await_count == 1
    query, records = conn.executemany.await_args.args
    assert "INSERT INTO data_engine.filings" in query
    assert len(records) == result["filingsSent"]
    assert isinstance(records[0][2], date) and isinstance(records[0][3], datetime)
    assert client.calls_made == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_filings_skipped_without_pool(mock_api):
    _mock_edgar(mock_api)
    client = _client()
    result = await edgar.sync_filings(client, "AAPL", pool=None, today=TODAY)
    assert result["stored"] is False and result["filingsSent"] == 0
    assert result["filingsFetched"] > 0  # fetched anyway
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_filings_stops_on_block(mock_api):
    mock_api.get(EDGAR_TICKERS_URL).mock(return_value=httpx.Response(429, text="slow"))
    pool, conn = _make_pool()
    client = _client()
    with pytest.raises(EdgarRateLimited):
        await edgar.sync_filings(client, "AAPL", pool=pool, today=TODAY)
    conn.executemany.assert_not_called()
    await client.aclose()


# ── Store: SQL + dedup semantics ─────────────────────────────────────────


def _row(accession="0000320193-26-000018", **kw):
    row = {
        "ticker": "AAPL",
        "form": "8-K",
        "filed_on": date(2026, 7, 30),
        "accepted_at": datetime(2026, 7, 30, 20, 30, 28, tzinfo=timezone.utc),
        "accession": accession,
        "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm",
        "meta": {"cik": AAPL_CIK, "items": "2.02,9.01"},
    }
    row.update(kw)
    return row


@pytest.mark.asyncio
async def test_upsert_filings_sql():
    pool, conn = _make_pool()
    sent = await upsert_filings(pool, [_row(), _row(accession="0000320193-26-000011", accepted_at=None)])
    assert sent == 2
    query, records = conn.executemany.await_args.args
    assert "INSERT INTO data_engine.filings" in query
    assert "ON CONFLICT (ticker, accession) DO NOTHING" in query
    assert "DO UPDATE" not in query
    r = _row()
    assert records[0] == ("AAPL", "8-K", r["filed_on"], r["accepted_at"], r["accession"], r["url"], json.dumps(r["meta"]))
    assert records[1][3] is None  # accepted_at stays NULL, never fabricated


@pytest.mark.asyncio
async def test_upsert_filings_dedups_in_batch():
    pool, conn = _make_pool()
    sent = await upsert_filings(pool, [
        _row(meta={"items": "first"}),
        _row(meta={"items": "second"}),
        _row(accession="0000320193-26-000011"),
    ])
    assert sent == 2
    _query, records = conn.executemany.await_args.args
    assert [r[4] for r in records] == ["0000320193-26-000018", "0000320193-26-000011"]
    assert json.loads(records[0][6]) == {"items": "first"}  # first kept, the answer DO NOTHING gives


@pytest.mark.asyncio
async def test_upsert_filings_drops_incomplete():
    pool, conn = _make_pool()
    sent = await upsert_filings(pool, [
        _row(accession=""),
        _row(url=""),
        _row(form=""),
        _row(filed_on=None),
        _row(ticker=""),
    ])
    assert sent == 0
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_filings_empty_is_noop():
    pool, conn = _make_pool()
    assert await upsert_filings(pool, []) == 0
    conn.executemany.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_filings_raise_propagates():
    pool, conn = _make_pool()
    conn.executemany = AsyncMock(side_effect=RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await upsert_filings(pool, [_row()])


def test_migration_004_shape():
    sql = MIGRATION.read_text()
    assert "CREATE TABLE IF NOT EXISTS data_engine.filings" in sql
    assert "PRIMARY KEY (ticker, accession)" in sql
    assert "ticker          VARCHAR(10) NOT NULL" in sql
    assert "filed_on        DATE NOT NULL" in sql
    assert "accepted_at     TIMESTAMPTZ," in sql  # nullable
    assert "filed_at" not in sql
    assert "IF NOT EXISTS" in sql and "INSERT INTO" not in sql
