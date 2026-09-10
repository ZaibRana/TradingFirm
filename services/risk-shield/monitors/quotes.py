"""
TradingFirm — core quotes for the regime monitors (Part 3.2).

One yf.download of the plan's 17 core tickers (daily, 1y), cached as an
envelope under tf:risk:cache:quotes:
    {asOf, period, interval,
     tickers: {SPY: {date[], open[], high[], low[], close[], volume[]}, …},
     missing: [...], reason: null | "partial" | "empty"}
5 min when every ticker came back, 120 s when degraded (decision 3).

REQUEST COUNT (read from the installed 1.5.1, decision 5): threads=False
loops Ticker(t).history() once per ticker, and each first fetches the
ticker's timezone (hard-coded timeout=10) unless yfinance's on-disk tz cache
has it. Cold container: 34 requests. Warm: 17. Worst case at timeout=5:
255 s cold, 85 s warm.

NO OUTER TIMEOUT, on purpose: the download runs in a thread, which cannot be
cancelled; a wait_for would release the single-flight lock while the thread
kept sending requests. The lock is held until the thread returns.

REFUSAL DETECTION (decision 6): 1.5.1 catches YFRateLimitError per ticker
and only logs it, so a handler on the "yfinance" logger watches the
download. Version-guarded because it depends on that log format.
"""

import asyncio
import gc
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from cache import (
    KIND_QUOTES,
    SOURCE_YFINANCE,
    TTL_COOLDOWN_YFINANCE,
    TTL_DEGRADED,
    TTL_QUOTES,
    cached_json,
    canonical,
    cooldown_remaining,
    risk_key,
    start_cooldown,
)
from monitors.errors import QuotesCoolingDown, QuotesError, QuotesRateLimited

logger = logging.getLogger(__name__)

EXPECTED_YF_VERSION = "1.5.1"

# Plan §2, in plan order. ≤ 20 per yf.download (CLAUDE.md).
CORE_TICKERS = tuple(canonical(t) for t in (
    "SPY", "QQQ", "RSP", "^VIX", "TLT", "GLD", "UUP", "XLK", "XLU",
    "XLP", "XLV", "XLY", "XLF", "ES=F", "NQ=F", "CL=F", "GC=F",
))
QUOTES_PERIOD = "1y"
QUOTES_INTERVAL = "1d"
YF_REQUEST_TIMEOUT = 5
QUOTES_REASONS = (None, "partial", "empty")
RATE_LIMIT_PATTERN = re.compile(r"YFRateLimitError|Too Many Requests")

_ENVELOPE_KEYS = ("asOf", "period", "interval", "tickers", "missing", "reason")
_COLUMNS = (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close"))

# Single-flight: one lock per running event loop (an asyncio.Lock binds to
# the loop it first waits on; the service runs one loop, tests run many).
_lock_state: dict[str, Any] = {"loop": None, "lock": None}


def _download_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _lock_state["loop"] is not loop:
        _lock_state["loop"] = loop
        _lock_state["lock"] = asyncio.Lock()
    return _lock_state["lock"]


# ── Download ─────────────────────────────────────────────────────

class _LogCapture(logging.Handler):
    """Collects every yfinance log message emitted during one download."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except Exception:
            self.messages.append(str(record.msg))


def is_rate_limited(messages: list[str]) -> bool:
    return any(RATE_LIMIT_PATTERN.search(m) for m in messages)


def _download_sync(tickers: list[str]) -> pd.DataFrame:
    # No session= (yfinance 1.x manages its own); threads=False (CLAUDE.md).
    return yf.download(
        " ".join(tickers),
        period=QUOTES_PERIOD,
        interval=QUOTES_INTERVAL,
        group_by="ticker",
        threads=False,
        progress=False,
        auto_adjust=True,
        timeout=YF_REQUEST_TIMEOUT,
    )


async def download_frame(tickers) -> tuple[Optional[pd.DataFrame], list[str]]:
    """
    Run the download in a thread with the yfinance logger watched.
    Returns (frame or None, captured log messages). A raised
    YFRateLimitError returns (None, messages + its repr) so the caller sees
    one signal; any other raise becomes QuotesError. The handler is removed
    on every path.
    """
    capture = _LogCapture()
    yf_logger = logging.getLogger("yfinance")
    yf_logger.addHandler(capture)
    try:
        df = await asyncio.to_thread(_download_sync, list(tickers))
    except YFRateLimitError as e:
        return None, capture.messages + [repr(e)]
    except Exception as e:
        raise QuotesError(f"core quotes download failed: {type(e).__name__}: {e}") from None
    finally:
        yf_logger.removeHandler(capture)
    return df, capture.messages


# ── Frame → envelope ─────────────────────────────────────────────

def _num(value: Any, integer: bool = False) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return int(f) if integer else f


def frame_to_tickers(df: Optional[pd.DataFrame], tickers) -> tuple[dict, list[str]]:
    """Columnar series per ticker; rows with a NaN close dropped; handles
    MultiIndex (Ticker, Price) columns and a flat single-ticker frame."""
    tickers = list(tickers)
    out: dict[str, dict] = {}
    if df is not None and not df.empty:
        multi = isinstance(df.columns, pd.MultiIndex)
        for t in tickers:
            if multi:
                if t not in df.columns.get_level_values(0):
                    continue
                sub = df[t]
            elif len(tickers) == 1:
                sub = df
            else:
                break
            if "Close" not in sub.columns:
                continue
            sub = sub.dropna(subset=["Close"])
            if sub.empty:
                continue
            series = {"date": [ts.strftime("%Y-%m-%d") for ts in sub.index]}
            for src, dst in _COLUMNS:
                series[dst] = [_num(v) for v in sub[src]] if src in sub.columns else [None] * len(sub)
            series["volume"] = (
                [_num(v, integer=True) for v in sub["Volume"]]
                if "Volume" in sub.columns else [None] * len(sub)
            )
            out[t] = series
    missing = [t for t in tickers if t not in out]
    return out, missing


def build_envelope(df: Optional[pd.DataFrame], tickers, now: Callable[[], datetime]) -> dict:
    series, missing = frame_to_tickers(df, tickers)
    if not missing:
        reason = None
    elif series:
        reason = "partial"
    else:
        reason = "empty"
    return {
        "asOf": now().isoformat(),
        "period": QUOTES_PERIOD,
        "interval": QUOTES_INTERVAL,
        "tickers": series,
        "missing": missing,
        "reason": reason,
    }


def quotes_ttl(body: dict) -> int:
    return TTL_DEGRADED if body.get("reason") is not None else TTL_QUOTES


def valid_quotes(body: Any) -> bool:
    return (
        isinstance(body, dict)
        and all(k in body for k in _ENVELOPE_KEYS)
        and body["reason"] in QUOTES_REASONS
        and isinstance(body["tickers"], dict)
        and isinstance(body["missing"], list)
        and set(body["tickers"]) | set(body["missing"]) == set(CORE_TICKERS)
    )


# ── Public ───────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_core_quotes(r, memory, *, now: Callable[[], datetime] = _utc_now) -> tuple[dict, bool]:
    """
    The 17 core tickers as (envelope, from_cache). Order: cache read →
    version guard → cooldown check → download. A refusal starts the
    source-wide cooldown and raises; an all-empty download is cached for
    120 s and also starts the cooldown (1.x's silent-block pattern).
    """
    async def fetch() -> dict:
        if yf.__version__ != EXPECTED_YF_VERSION:
            raise QuotesError(
                f"yfinance {yf.__version__} installed, rate-limit detection is "
                f"written for {EXPECTED_YF_VERSION}"
            )
        left = await cooldown_remaining(r, memory, SOURCE_YFINANCE, TTL_COOLDOWN_YFINANCE)
        if left is not None:
            raise QuotesCoolingDown(left)

        df, messages = await download_frame(CORE_TICKERS)
        try:
            if is_rate_limited(messages):
                await start_cooldown(r, memory, SOURCE_YFINANCE, TTL_COOLDOWN_YFINANCE)
                raise QuotesRateLimited("yfinance rate limited the core quotes download")
            body = build_envelope(df, CORE_TICKERS, now)
        finally:
            del df          # G8: nothing but the JSON envelope survives
            gc.collect()

        if body["reason"] == "empty":
            await start_cooldown(r, memory, SOURCE_YFINANCE, TTL_COOLDOWN_YFINANCE)
            logger.warning("Core quotes: all 17 tickers empty, yfinance cooldown started")
        elif body["reason"] == "partial":
            logger.warning(f"Core quotes: partial, missing {body['missing']}")
        return body

    async with _download_lock():
        return await cached_json(
            r, risk_key(KIND_QUOTES), None, fetch, valid=valid_quotes, ttl_for=quotes_ttl
        )
