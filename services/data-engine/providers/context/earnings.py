"""
TradingFirm — Earnings report dates (Part 2.3, commit 1).

Where past report dates come from, and how they are validated before they
reach data_engine.events.

  Primary   yfinance Ticker.get_earnings_dates(limit=12), through the
            DataProvider interface (provider.get_earnings_dates).
  Fallback  Alpha Vantage EARNINGS, used ONLY when the primary has no
            usable past date.

Why a fallback at all: the Finnhub free tier's /calendar/earnings returns
only the *upcoming* report (docs/decisions.md 2026-09-09), so Part 2.1
stores no past report dates and the reaction history would be empty.

The fallback is deliberately NOT tried in two cases:
  * the primary was rate limited (ProviderRateLimited) — the source
    refused to answer, which says nothing about whether it has data, and
    a second provider does not fix our being throttled (G6);
  * the bar store is empty — validation is then impossible, which is our
    failure and not the source's, so an Alpha Vantage call is wasted.

Validation (spec 2.3 rule 3): a past report date must be a stored daily
bar date or within one calendar day of one. Bar dates come from the store
(db.get_bars, whole daily history), never from the refresh frame: refresh
downloads 2 years while the yfinance feed reaches ~6 years back, so the
frame would drop older reports the store can still explain.

Rows older than the stored bar history are "out of range", not "dropped":
they can never be explained by bars we hold, so counting them as a data
quality problem would make dataQuality.dropped meaningless (AAPL's live
feed carries 25 rows over six years against a two-year store).

Everything written here lives under meta.earnings — one nested key, so
2.1's meta.calendar survives the `existing || new` merge in upsert_events.
"""

import gc
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from providers.base import ProviderRateLimited
from providers.context.alphavantage_client import (
    AlphaVantageClient,
    AlphaVantageError,
)
from tickers import validate_ticker

logger = logging.getLogger(__name__)

EVENT_EARNINGS = "earnings"
META_KEY = "earnings"

SOURCE_YFINANCE = "yfinance"
SOURCE_ALPHAVANTAGE = "alphavantage"

REASON_RATE_LIMITED = "rate_limited"
REASON_DOWN = "down"
REASON_NO_BARS = "no_bars"
REASON_ERROR = "error"

HOUR_AMC = "amc"
HOUR_BMO = "bmo"
HOUR_DMH = "dmh"

# yfinance 1.5.1 column names, confirmed live 2026-09-09 (spec §Live check).
COL_ESTIMATE = "EPS Estimate"
COL_REPORTED = "Reported EPS"
COL_SURPRISE = "Surprise(%)"

# Alpha Vantage writes the string "None" for a missing number.
AV_NULLS = {"", "none", "null", "-"}


def _event_at(d: date) -> datetime:
    """Midnight UTC of a calendar date — the same key finnhub.calendar_events
    builds, so a Finnhub projection and a confirmation land on one PK row."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _float(value: Any) -> Optional[float]:
    """Alpha Vantage numbers arrive as strings; NaN/'None' become null."""
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().lower() in AV_NULLS:
            return None
        try:
            return float(value)
        except ValueError:
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None (JSON null)


def _hour_from_eastern(ts) -> Optional[str]:
    """amc / bmo / dmh from the exchange-local report time."""
    try:
        local = ts.tz_convert("America/New_York") if ts.tzinfo else ts
    except (AttributeError, TypeError):
        local = ts
    minutes = local.hour * 60 + local.minute
    if minutes >= 16 * 60:
        return HOUR_AMC
    if minutes < 9 * 60 + 30:
        return HOUR_BMO
    return HOUR_DMH


def _row(ticker: str, d: date, source: str, hour, estimate, reported, surprise) -> dict:
    return {
        "ticker": ticker,
        "event_type": EVENT_EARNINGS,
        "event_at": _event_at(d),
        "meta": {META_KEY: {
            "source": source,
            "validated": False,
            "hour": hour,
            "epsEstimate": estimate,
            "epsReported": reported,
            "surprisePct": surprise,
        }},
    }


# ── Pure converters: source payload → event rows ─────────────────────────


def earnings_events_from_df(ticker: str, df) -> list[dict]:
    """
    yfinance get_earnings_dates() frame → ('earnings', date) rows.

    Index: tz-aware report timestamps in exchange local time. An absent
    column reads as all-None (fail open, logged once); a NaT index entry
    drops its row; NaN cells become JSON null.
    """
    t = validate_ticker(ticker)
    if df is None or getattr(df, "empty", True):
        return []

    missing = [c for c in (COL_ESTIMATE, COL_REPORTED, COL_SURPRISE) if c not in df.columns]
    if missing:
        logger.warning(f"earnings_events_from_df {t}: columns absent, read as null: {missing}")

    rows, dropped = [], 0
    for ts, item in df.iterrows():
        if ts is None or ts != ts:  # NaT
            dropped += 1
            continue
        try:
            d = ts.date()
        except AttributeError:
            dropped += 1
            continue
        rows.append(_row(
            t, d, SOURCE_YFINANCE, _hour_from_eastern(ts),
            _float(item.get(COL_ESTIMATE)),
            _float(item.get(COL_REPORTED)),
            _float(item.get(COL_SURPRISE)),
        ))
    if dropped:
        logger.warning(f"earnings_events_from_df {t}: dropped {dropped} row(s) without a date")
    return rows


def earnings_events_from_av(ticker: str, body: Any) -> list[dict]:
    """
    Alpha Vantage EARNINGS body → ('earnings', date) rows from
    quarterlyEarnings[].reportedDate. Values arrive as strings.
    """
    t = validate_ticker(ticker)
    if not isinstance(body, dict):
        return []
    items = body.get("quarterlyEarnings")
    if not isinstance(items, list):
        return []

    rows, dropped = [], 0
    for item in items:
        if not isinstance(item, dict):
            dropped += 1
            continue
        raw = (item.get("reportedDate") or "").strip()
        try:
            d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            dropped += 1
            continue
        report_time = (item.get("reportTime") or "").strip().lower()
        hour = {"post-market": HOUR_AMC, "pre-market": HOUR_BMO}.get(report_time)
        rows.append(_row(
            t, d, SOURCE_ALPHAVANTAGE, hour,
            _float(item.get("estimatedEPS")),
            _float(item.get("reportedEPS")),
            _float(item.get("surprisePercentage")),
        ))
    if dropped:
        logger.warning(f"earnings_events_from_av {t}: dropped {dropped} item(s) without a date")
    return rows


# ── Validation against the bar store ─────────────────────────────────────


def validate_report_dates(
    rows: list[dict],
    bar_dates: set,
    today: Optional[date] = None,
) -> tuple[list[dict], int, int]:
    """
    Split source rows into (kept, dropped, out_of_range).

    kept          future dates (validated False — nothing to check them
                  against yet) and past dates that are a stored bar date
                  or within one calendar day of one (validated True).
    dropped       past dates inside the stored range that are neither.
                  A real data-quality signal.
    out_of_range  past dates older than the stored history. Not a quality
                  problem: no bars, so no reaction, so nothing to say.
    """
    today = today or datetime.now(timezone.utc).date()
    if not bar_dates:
        return [], 0, len(rows)

    oldest = min(bar_dates)
    kept, dropped, out_of_range = [], 0, 0
    for row in rows:
        d = row["event_at"].date()
        if d > today:
            row["meta"][META_KEY]["validated"] = False
            kept.append(row)
            continue
        if d < oldest - timedelta(days=1):
            out_of_range += 1
            continue
        if any(d + timedelta(days=delta) in bar_dates for delta in (0, -1, 1)):
            row["meta"][META_KEY]["validated"] = True
            kept.append(row)
        else:
            dropped += 1
    return kept, dropped, out_of_range


def _has_past_validated(rows: list[dict], today: date) -> bool:
    return any(
        r["event_at"].date() <= today and r["meta"][META_KEY]["validated"] for r in rows
    )


# ── Fetch + store ────────────────────────────────────────────────────────


async def alphavantage_earnings(client: AlphaVantageClient, ticker: str) -> Any:
    """One Alpha Vantage EARNINGS call. Raises AlphaVantageError family."""
    return await client.get("EARNINGS", validate_ticker(ticker))


async def sync_earnings_dates(
    provider,
    av_client: Optional[AlphaVantageClient],
    ticker: str,
    pool,
    *,
    today: Optional[date] = None,
) -> dict:
    """
    Fetch report dates for `ticker`, validate them against the stored daily
    bars and upsert them as ('earnings', date) rows with meta.earnings.

    Returns one shape, always:
        {"source": str|None, "stored": int, "dropped": int, "reason": str|None}
    reason: None on success, else "rate_limited" | "down" | "no_bars".
    """
    t = validate_ticker(ticker)
    today = today or datetime.now(timezone.utc).date()
    from db import get_bars, upsert_events  # deferred: db.py imports asyncpg

    result = {"source": None, "stored": 0, "dropped": 0, "reason": None}

    bars = await get_bars(pool, t, "1d")
    bar_dates = {b["ts"].date() for b in bars}
    del bars
    if not bar_dates:
        logger.warning(f"sync_earnings_dates {t}: no stored daily bars, cannot validate")
        result["reason"] = REASON_NO_BARS
        return result

    # ── Primary: yfinance through the provider ──────────────────────────
    try:
        df = await provider.get_earnings_dates(t)
    except ProviderRateLimited as e:
        logger.warning(f"sync_earnings_dates {t}: primary rate limited, no fallback ({e})")
        result["reason"] = REASON_RATE_LIMITED
        return result
    except Exception as e:
        logger.warning(f"sync_earnings_dates {t}: primary failed: {type(e).__name__}: {e}")
        df = None

    primary_rows = earnings_events_from_df(t, df)
    if df is not None:
        del df
        gc.collect()
    kept, dropped, out_of_range = validate_report_dates(primary_rows, bar_dates, today)
    logger.info(
        f"sync_earnings_dates {t}: yfinance {len(primary_rows)} rows → "
        f"{len(kept)} kept, {dropped} dropped, {out_of_range} outside bar history"
    )

    rows, source = kept, None
    if _has_past_validated(kept, today):
        result.update(source=SOURCE_YFINANCE, dropped=dropped)
    else:
        # ── Fallback: Alpha Vantage ─────────────────────────────────────
        av_body = None
        if av_client is None:
            logger.warning(f"sync_earnings_dates {t}: no Alpha Vantage client, fallback skipped")
        else:
            try:
                av_body = await alphavantage_earnings(av_client, t)
            except AlphaVantageError as e:
                logger.warning(f"sync_earnings_dates {t}: fallback unavailable: {e}")

        av_rows = earnings_events_from_av(t, av_body)
        av_kept, av_dropped, av_out = validate_report_dates(av_rows, bar_dates, today)
        logger.info(
            f"sync_earnings_dates {t}: alphavantage {len(av_rows)} rows → "
            f"{len(av_kept)} kept, {av_dropped} dropped, {av_out} outside bar history"
        )
        dropped += av_dropped
        if _has_past_validated(av_kept, today):
            # Keep the primary's future rows: 6.4 needs the upcoming date.
            rows = kept + av_kept
            source = SOURCE_ALPHAVANTAGE
        else:
            rows = kept + av_kept
            source = None
        result.update(source=source, dropped=dropped)
        if source is None and not rows:
            result["reason"] = REASON_DOWN
            return result
        if source is None:
            result["reason"] = REASON_DOWN

    for sample in rows[:3]:
        meta = sample["meta"][META_KEY]
        logger.info(
            f"earnings {t}: {sample['event_at'].date()} {meta['source']} "
            f"hour={meta['hour']} validated={meta['validated']}"
        )

    result["stored"] = await upsert_events(pool, rows)
    return result
