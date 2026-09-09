"""
TradingFirm — SEC EDGAR filings fetcher (Part 2.2). Spec: docs/specs/2.2.md.

Two fetchers over EdgarClient, each Redis-cached through cache.cached_json:

    cik_map(client)                          company_tickers.json   TTL 24 h
    recent_filings(client, ticker,           submissions/CIK….json  TTL 15 min
                   forms=('8-K', '4'), days=30)

The ticker → CIK map is one ~10k-entry file cached whole under one key
(EDGAR_CIK_MAP_KEY); lookups are then free. Submissions bodies are ~1 MB
for large issuers, so the cache holds the *parsed* rows of `filings.recent`
whose filingDate is within the last MAX_DAYS days (every form, newest
first) plus the oldest filingDate the block reaches, under
edgar_key('filings', ticker) — not the raw body — and `forms` / `days`
(1–MAX_DAYS) filter in-process on every call.

recent_filings returns (rows, truncated). `truncated` is True only when
the oldest row of the whole `recent` block, before any filtering, is newer
than today − days: the block did not reach back far enough (EDGAR pages
older filings into `filings.files`, which this part does not fetch).

Filing dict (JSON-safe; the cache and the fetcher return these):
    ticker, form, filed_on ('YYYY-MM-DD', the official filingDate),
    accepted_at ('…T…Z' or None, the acceptanceDateTime), accession, url,
    meta {cik, reportDate, primaryDocument, primaryDocDescription, items}
`filing_records()` turns them into db.upsert_filings() rows (filed_on →
date, accepted_at → aware UTC datetime or None). Amendments match their
base form ('8-K/A' matches '8-K'); the row keeps the literal form.

Failure policy: Redis absent or failing is fail-open (fetch uncached). A
ticker missing from the CIK map, or a CIK whose submissions file is 404,
returns ([], False) with a warning: the SEC has nothing for it, which is
normal for foreign issuers. An empty CIK map raises (never cached).
Unequal `recent` columns raise EdgarError and nothing is cached: a short
middle array would pair every later accession with the wrong form.
Other client errors propagate as the typed EdgarError family. No retries.
"""

import gc
import logging
from datetime import date, datetime, timedelta, timezone

from cache import TTL_EDGAR_CIK_MAP, TTL_EDGAR_FILINGS, EDGAR_CIK_MAP_KEY, cached_json, edgar_key
from providers.context.edgar_client import (
    EDGAR_ARCHIVES_BASE,
    EDGAR_TICKERS_URL,
    EdgarClient,
    EdgarError,
    EdgarNotFound,
    submissions_url,
)
from tickers import normalize_ticker, validate_ticker

logger = logging.getLogger(__name__)

KIND_FILINGS = "filings"
DEFAULT_FORMS = ("8-K", "4")
DEFAULT_DAYS = 30
MAX_DAYS = 90

_RECENT_COLUMNS = (
    "accessionNumber", "filingDate", "reportDate", "acceptanceDateTime",
    "form", "primaryDocument", "primaryDocDescription", "items",
)


# ── Pure helpers ─────────────────────────────────────────────────────────


def base_form(form: str) -> str:
    """'8-K/A' → '8-K', ' 4 ' → '4'. Amendments fold into their base form."""
    return (form or "").strip().upper().split("/")[0]


def filing_url(cik: int, accession: str, primary_doc: str | None) -> str:
    """Archive URL of a filing's primary document, or of its index page
    when the submissions row names no document."""
    folder = accession.replace("-", "")
    doc = (primary_doc or "").strip().lstrip("/")
    if not doc:
        doc = f"{accession}-index.htm"
    return f"{EDGAR_ARCHIVES_BASE}/{int(cik)}/{folder}/{doc}"


def parse_cik_map(body) -> dict[str, int]:
    """company_tickers.json ({"0": {"cik_str": 320193, "ticker": "AAPL",
    "title": ...}, ...}) → {normalized ticker: cik}. Entries without a
    ticker or a numeric cik_str are skipped and counted. Keys pass through
    normalize_ticker only, so hyphenated class shares ('BRK-B') are keys;
    the lookup side (cik_for) applies validate_ticker, which rejects them —
    deferred, docs/progress.md row 2.2."""
    out: dict[str, int] = {}
    if not isinstance(body, dict):
        return out
    skipped = 0
    for entry in body.values():
        if not isinstance(entry, dict):
            skipped += 1
            continue
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        try:
            cik = int(cik)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not ticker or not str(ticker).strip():
            skipped += 1
            continue
        out[normalize_ticker(str(ticker))] = cik
    if skipped:
        logger.warning(f"parse_cik_map: skipped {skipped} entr(y/ies) without ticker/cik_str")
    return out


def parse_submissions(
    ticker: str,
    cik: int,
    body,
    *,
    today: date,
    max_days: int = MAX_DAYS,
) -> tuple[list[dict], date | None]:
    """
    The `filings.recent` block of a submissions body (parallel arrays,
    newest first) → (rows filed within the last `max_days` days, every
    form; the oldest filingDate anywhere in the block, or None). Rows
    without an accession number, a form or a parseable filingDate are
    dropped and counted (they cannot contribute to `oldest` either). A
    column that is absent altogether reads as all-None; present columns of
    unequal length raise EdgarError — a short middle array would shift
    every later accession onto the wrong form.
    """
    t = validate_ticker(ticker)
    recent = {}
    if isinstance(body, dict):
        filings = body.get("filings")
        if isinstance(filings, dict) and isinstance(filings.get("recent"), dict):
            recent = filings["recent"]
    present = {name: recent.get(name) for name in _RECENT_COLUMNS if isinstance(recent.get(name), list)}
    if not present.get("accessionNumber"):
        return [], None
    lengths = {name: len(col) for name, col in present.items()}
    if len(set(lengths.values())) > 1:
        raise EdgarError(f"submissions {t}: unequal recent columns {lengths}")
    n = lengths["accessionNumber"]
    columns = {name: present.get(name) or [None] * n for name in _RECENT_COLUMNS}

    since = today - timedelta(days=max_days)
    rows: list[dict] = []
    oldest: date | None = None
    dropped = 0
    for i in range(n):
        accession = (columns["accessionNumber"][i] or "").strip()
        form = (columns["form"][i] or "").strip()
        filed = _day(columns["filingDate"][i])
        if not accession or not form or filed is None:
            dropped += 1
            continue
        if oldest is None or filed < oldest:
            oldest = filed
        if filed < since:
            continue
        rows.append({
            "ticker": t,
            "form": form,
            "filed_on": filed.isoformat(),
            "accepted_at": columns["acceptanceDateTime"][i] or None,
            "accession": accession,
            "url": filing_url(cik, accession, columns["primaryDocument"][i]),
            "meta": {
                "cik": int(cik),
                "reportDate": columns["reportDate"][i] or None,
                "primaryDocument": columns["primaryDocument"][i] or None,
                "primaryDocDescription": columns["primaryDocDescription"][i] or None,
                "items": columns["items"][i] or None,
            },
        })
    if dropped:
        logger.warning(f"parse_submissions {t}: dropped {dropped} row(s) missing accession/form/filingDate")
    return rows, oldest


def filter_filings(filings: list[dict], forms, days: int, today: date) -> list[dict]:
    """Keep filings whose base form is in `forms` and whose filed_on is
    within the last `days` days (inclusive of the boundary day). Newest
    first."""
    wanted = {base_form(f) for f in forms}
    since = today - timedelta(days=days)
    kept = [
        f for f in filings
        if base_form(f.get("form", "")) in wanted
        and (d := _day(f.get("filed_on"))) is not None
        and since <= d <= today
    ]
    kept.sort(key=lambda f: f["filed_on"], reverse=True)
    return kept


def filing_records(filings: list[dict]) -> list[dict]:
    """Filing dicts → db.upsert_filings() rows: filed_on as a date,
    accepted_at as an aware UTC datetime or None (never fabricated)."""
    rows = []
    for f in filings:
        filed = _day(f.get("filed_on"))
        if filed is None:
            continue
        rows.append({
            "ticker": f["ticker"],
            "form": f["form"],
            "filed_on": filed,
            "accepted_at": _timestamp(f.get("accepted_at")),
            "accession": f["accession"],
            "url": f["url"],
            "meta": f.get("meta") or {},
        })
    return rows


def _day(value) -> date | None:
    """'YYYY-MM-DD…' → date, None if missing or malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _timestamp(value) -> datetime | None:
    """'2026-08-01T20:31:02.000Z' → aware UTC datetime, None if missing or
    malformed. EDGAR's acceptanceDateTime is UTC with a trailing Z
    (verified live: a Form 4 accepted 18:30 ET shows 22:30Z)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ── Fetchers ─────────────────────────────────────────────────────────────


async def cik_map(client: EdgarClient, *, redis=None) -> dict[str, int]:
    """{ticker: cik} for every SEC registrant, cached 24 h under one key.
    An empty map raises EdgarError and is never cached."""

    async def fetch():
        body = await client.get_json(EDGAR_TICKERS_URL)
        mapping = parse_cik_map(body)
        del body
        gc.collect()
        if not mapping:
            raise EdgarError("company_tickers.json parsed to an empty map")
        logger.info(f"cik_map: {len(mapping)} tickers; sample {list(mapping.items())[:3]}")
        return mapping

    mapping, _ = await cached_json(
        redis, EDGAR_CIK_MAP_KEY, TTL_EDGAR_CIK_MAP, fetch,
        valid=lambda b: isinstance(b, dict) and bool(b),
    )
    return {k: int(v) for k, v in mapping.items()}


async def cik_for(client: EdgarClient, ticker: str, *, redis=None) -> int | None:
    """CIK for one ticker, or None when EDGAR does not list it."""
    t = validate_ticker(ticker)
    mapping = await cik_map(client, redis=redis)
    cik = mapping.get(t)
    if cik is None:
        logger.warning(f"cik_for {t}: not in company_tickers.json")
    return cik


async def recent_filings(
    client: EdgarClient,
    ticker: str,
    *,
    forms=DEFAULT_FORMS,
    days: int = DEFAULT_DAYS,
    redis=None,
    today: date | None = None,
) -> tuple[list[dict], bool]:
    """
    (filings of the given base forms filed in the last `days` days, newest
    first; truncated). Two HTTP calls at most (fewer on cache hits).
    ([], False) when the SEC has nothing for the ticker (not in the map, or
    submissions 404 — both logged at warning).
    """
    t = validate_ticker(ticker)
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be an int in 1..{MAX_DAYS}, got {days!r}")
    if not forms:
        raise ValueError("forms must name at least one form")
    today = today or datetime.now(timezone.utc).date()

    cik = await cik_for(client, t, redis=redis)
    if cik is None:
        return [], False

    async def fetch():
        try:
            body = await client.get_json(submissions_url(cik))
        except EdgarNotFound:
            logger.warning(f"recent_filings {t}: submissions 404 for CIK {cik}, treating as no filings")
            return {"rows": [], "oldest": None}
        rows, oldest = parse_submissions(t, cik, body, today=today)
        del body
        gc.collect()
        for sample in rows[:3]:
            logger.info(f"filing {t}: {sample['filed_on']} {sample['form']} {sample['accession']}")
        return {"rows": rows, "oldest": oldest.isoformat() if oldest else None}

    cached, _ = await cached_json(
        redis, edgar_key(KIND_FILINGS, t), TTL_EDGAR_FILINGS, fetch,
        valid=lambda b: isinstance(b, dict) and isinstance(b.get("rows"), list),
    )
    rows = filter_filings(cached["rows"], forms, days, today)

    since = today - timedelta(days=days)
    oldest = _day(cached.get("oldest"))
    truncated = oldest is not None and oldest > since
    if truncated:
        logger.warning(
            f"recent_filings {t}: recent block reaches back only to {oldest}, window starts {since}; "
            f"older filings sit in the paged files this part does not fetch"
        )
    return rows, truncated


# ── Fetch + store ────────────────────────────────────────────────────────


async def sync_filings(
    client: EdgarClient,
    ticker: str,
    *,
    pool=None,
    redis=None,
    forms=DEFAULT_FORMS,
    days: int = DEFAULT_DAYS,
    today: date | None = None,
) -> dict:
    """
    Fetch recent filings for `ticker` and store them through
    db.upsert_filings(). With no db pool the fetch still happens and
    storage is skipped (logged).

    Returns {"ticker", "filingsFetched", "filingsSent", "truncated",
    "stored"}. EDGAR errors propagate; db errors propagate.
    """
    t = validate_ticker(ticker)
    from db import upsert_filings  # deferred: db.py imports asyncpg

    filings, truncated = await recent_filings(client, t, forms=forms, days=days, redis=redis, today=today)
    rows = filing_records(filings)

    result = {"ticker": t, "filingsFetched": len(filings), "filingsSent": 0, "truncated": truncated, "stored": False}
    if pool is None:
        logger.warning(f"sync_filings {t}: no db pool, {len(rows)} filings not stored")
        return result

    result["filingsSent"] = await upsert_filings(pool, rows)
    result["stored"] = True
    return result
