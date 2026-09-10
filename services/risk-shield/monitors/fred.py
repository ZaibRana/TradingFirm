"""
TradingFirm — FRED macro series (Part 3.2).

get_series() returns one series as an envelope, cached per series:
    {seriesId, asOf, observationStart, observations: [{date, value}],
     dropped, reason: null | "empty"}
6 h when the answer is full, 120 s when it is degraded (decision 3).
fred_snapshot() walks the plan's 8 series; a source-wide state stops the
walk, a per-series error does not (decision 8).

No yfinance or pandas import here: monitors/__init__.py stays empty so this
module loads without them.
"""

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from cache import (
    KIND_FRED,
    KIND_FRED_LAST,
    SOURCE_FRED,
    TTL_COOLDOWN_FRED,
    TTL_COOLDOWN_FRED_AUTH,
    TTL_DEGRADED,
    TTL_FRED,
    TTL_FRED_LAST_KNOWN,
    cached_json,
    canonical,
    cooldown_remaining,
    get_cached_json,
    risk_key,
    set_cached_json,
    start_cooldown,
)
from monitors.errors import (
    FredCoolingDown,
    FredError,
    FredNotAuthorized,
    FredNotConfigured,
    FredRateLimited,
    FredSourceWide,
)

logger = logging.getLogger(__name__)

# Plan §2, in plan order.
FRED_SERIES = ("VIXCLS", "DGS10", "DGS2", "T10Y2Y", "DFF", "DCOILWTICO", "CPIAUCSL", "UNRATE")
FRED_REASONS = (None, "empty")
_ENVELOPE_KEYS = ("seriesId", "asOf", "observationStart", "observations", "dropped", "reason")


def normalize_series(series_id: Any) -> str:
    """canonical() plus the allowlist. Raises ValueError before any request."""
    sid = canonical(series_id)
    if sid not in FRED_SERIES:
        raise ValueError(f"unknown FRED series: {sid!r}")
    return sid


def fred_ttl(body: dict) -> int:
    return TTL_DEGRADED if body.get("reason") is not None else TTL_FRED


def _valid_for(sid: str) -> Callable[[Any], bool]:
    def valid(body: Any) -> bool:
        return (
            isinstance(body, dict)
            and all(k in body for k in _ENVELOPE_KEYS)
            and body["seriesId"] == sid
            and isinstance(body["observations"], list)
            and body["reason"] in FRED_REASONS
        )
    return valid


def observations_to_envelope(sid: str, raw: dict, observation_start: str, as_of: datetime) -> dict:
    """Keep numeric observations; drop "." (FRED's missing marker) and any
    other non-numeric or non-finite value, counting them."""
    kept = []
    dropped = 0
    for obs in raw.get("observations") or []:
        if not isinstance(obs, dict) or not isinstance(obs.get("date"), str):
            dropped += 1
            continue
        try:
            value = float(obs.get("value"))
        except (TypeError, ValueError):
            dropped += 1
            continue
        if not math.isfinite(value):
            dropped += 1
            continue
        kept.append({"date": obs["date"], "value": value})
    return {
        "seriesId": sid,
        "asOf": as_of.isoformat(),
        "observationStart": observation_start,
        "observations": kept,
        "dropped": dropped,
        "reason": None if kept else "empty",
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_series(
    r,
    memory,
    client,
    series_id: str,
    *,
    now: Callable[[], datetime] = _utc_now,
) -> tuple[dict, bool]:
    """
    One series as (envelope, from_cache). Order: cache read → cooldown
    check → request. Refusals start the source-wide cooldown and raise;
    nothing is cached for a raise.
    """
    sid = normalize_series(series_id)

    async def fetch() -> dict:
        # Redis TTL is authoritative; the memory fallback cannot tell a 429
        # from an auth refusal, so it remembers either for TTL_COOLDOWN_FRED.
        left = await cooldown_remaining(r, memory, SOURCE_FRED, TTL_COOLDOWN_FRED)
        if left is not None:
            raise FredCoolingDown(left)
        try:
            raw = await client.observations(sid)
        except FredRateLimited:
            await start_cooldown(r, memory, SOURCE_FRED, TTL_COOLDOWN_FRED)
            raise
        except FredNotAuthorized:
            await start_cooldown(r, memory, SOURCE_FRED, TTL_COOLDOWN_FRED_AUTH)
            raise
        body = observations_to_envelope(sid, raw, client.observation_start(), now())
        if body["dropped"]:
            logger.info(f"FRED {sid}: dropped {body['dropped']} non-numeric observations")
        if body["reason"] is None:
            await write_last_known(r, body)
        return body

    return await cached_json(
        r, risk_key(KIND_FRED, sid), None, fetch, valid=_valid_for(sid), ttl_for=fred_ttl
    )


_SOURCE_WIDE_STATUS = (
    (FredCoolingDown, "cooldown"),
    (FredRateLimited, "rate_limited"),
    (FredNotAuthorized, "not_authorized"),
    (FredNotConfigured, "unconfigured"),
)


def _failed(status: str) -> dict:
    return {"status": status, "cached": False, "observations": [], "dropped": 0}


async def fred_snapshot(r, memory, client, *, now: Callable[[], datetime] = _utc_now) -> dict:
    """
    All 8 series, in plan order: {SERIES: {status, cached, observations,
    dropped}}. Not cached itself — each series is. Never None anywhere.
    """
    out: dict[str, dict] = {}
    stopped_by: Optional[str] = None
    for sid in FRED_SERIES:
        if stopped_by is not None:
            out[sid] = _failed("skipped")
            continue
        try:
            body, from_cache = await get_series(r, memory, client, sid, now=now)
        except FredSourceWide as e:
            status = next((s for cls, s in _SOURCE_WIDE_STATUS if isinstance(e, cls)), "error")
            out[sid] = _failed(status)
            stopped_by = status
            logger.warning(f"FRED snapshot stopped at {sid}: {e}")
        except FredError as e:
            out[sid] = _failed("error")
            logger.warning(f"FRED {sid} failed, continuing: {e}")
        else:
            out[sid] = {
                "status": "ok" if body["reason"] is None else body["reason"],
                "cached": from_cache,
                "observations": body["observations"],
                "dropped": body["dropped"],
            }
    return out


# ── Last-known envelope and the view (Part 3.6a, spec decision 3) ─
# Option (b) from 3.3: the fetcher keeps the last full envelope per series,
# so a refusal, cooldown, error or empty answer is stale data, not an error.
# No process-memory copy: without Redis there is no last-known (3.3's rule).

VIEW_SOURCES = ("fresh", "cache", "last_known", "none")
STALE_REASONS = (None, "age", "last_known", "no_data")


async def write_last_known(r, body: dict) -> None:
    """Keep a full envelope for TTL_FRED_LAST_KNOWN. A write failure is
    logged and never fails the fetch; the previous copy stands until its TTL."""
    if r is None:
        return
    try:
        await set_cached_json(r, risk_key(KIND_FRED_LAST, body["seriesId"]), body, TTL_FRED_LAST_KNOWN)
    except Exception as e:
        logger.warning(f"Last-known FRED write failed for {body['seriesId']}: {e}")


async def read_last_known(r, series_id: str) -> Optional[dict]:
    """The last full envelope for a series, or None when Redis is absent or
    raising, the key is missing, or it holds the wrong shape (another
    series' id, a degraded envelope, not an envelope)."""
    if r is None:
        return None
    sid = normalize_series(series_id)
    key = risk_key(KIND_FRED_LAST, sid)
    try:
        body = await get_cached_json(r, key)
    except Exception as e:
        logger.warning(f"Last-known FRED read failed for {sid}: {e}")
        return None
    if body is None:
        return None
    if not _valid_for(sid)(body) or body["reason"] is not None:
        logger.warning(f"Cache at {key} has the wrong shape, ignoring")
        return None
    return body


# ── Freshness by series cadence (Part 3.6a, spec decision 4) ─────
# (cadence, maxAgeDays): the most calendar days (ET) the latest observation
# may lag before the series reads stale. Provisional. The 2026-09-10 step 0
# live check found every daily series 1 day behind (DGS10 skips Labor Day),
# CPIAUCSL 71 days (the eve of a release; ~74 is the longest normal age) and
# UNRATE 40 (~62 the longest normal). DCOILWTICO was 1 day that run but ~9
# days in 3.2's canary, hence 14. Daily 6 = a Thanksgiving-length gap + lag.
FRED_CADENCE = {
    "VIXCLS": ("daily", 6),
    "DGS10": ("daily", 6),
    "DGS2": ("daily", 6),
    "T10Y2Y": ("daily", 6),
    "DFF": ("daily", 6),
    "DCOILWTICO": ("daily", 14),
    "CPIAUCSL": ("monthly", 80),
    "UNRATE": ("monthly", 70),
}
ET = ZoneInfo("America/New_York")
MONTH_AGO_DAYS = 30
YEAR_AGO_DAYS = 365


def _on_or_before(observations: list, day: date) -> Optional[dict]:
    """The last observation dated on or before `day` (observations ascend)."""
    for obs in reversed(observations):
        if date.fromisoformat(obs["date"]) <= day:
            return obs
    return None


def compact_points(observations: list) -> dict:
    """{latest, monthAgo, yearAgo}: the last observation, and the last one on
    or before latest − 30 / − 365 days. None where history is too short. The
    arrays themselves never leave the view."""
    if not observations:
        return {"latest": None, "monthAgo": None, "yearAgo": None}
    latest = observations[-1]
    latest_day = date.fromisoformat(latest["date"])
    return {
        "latest": latest,
        "monthAgo": _on_or_before(observations, latest_day - timedelta(days=MONTH_AGO_DAYS)),
        "yearAgo": _on_or_before(observations, latest_day - timedelta(days=YEAR_AGO_DAYS)),
    }


def _series_view(sid: str, status: str, source: str, stale_reason: Optional[str],
                 observations: list, today: date) -> dict:
    """One series for the inputs. Stale reasons, first match: no_data,
    last_known, then age (the latest observation older than maxAgeDays)."""
    cadence, max_age = FRED_CADENCE[sid]
    points = compact_points(observations)
    age = (today - date.fromisoformat(points["latest"]["date"])).days if points["latest"] else None
    if stale_reason is None and age is not None and age > max_age:
        stale_reason = "age"
    return {
        "status": status,
        "source": source,
        "stale": stale_reason is not None,
        "staleReason": stale_reason,
        "cadence": cadence,
        "maxAgeDays": max_age,
        "ageDays": age,
        **points,
    }


async def get_fred_view(r, memory, client, *, now: Callable[[], datetime] = _utc_now) -> dict:
    """
    What the macro inputs read: {SERIES: {status, source, stale, staleReason,
    cadence, maxAgeDays, ageDays, latest, monthAgo, yearAgo}} for the 8
    series in plan order. fred_snapshot unchanged, then every series whose
    status is not "ok" is filled from last-known with stale: true, and every
    series is judged against its cadence on the ET date. Never raises for a
    source state.
    """
    today = now().astimezone(ET).date()
    snapshot = await fred_snapshot(r, memory, client, now=now)
    view: dict[str, dict] = {}
    for sid in FRED_SERIES:
        entry = snapshot[sid]
        if entry["status"] == "ok":
            source = "cache" if entry["cached"] else "fresh"
            view[sid] = _series_view(sid, "ok", source, None, entry["observations"], today)
            continue
        last = await read_last_known(r, sid)
        if last is None:
            view[sid] = _series_view(sid, entry["status"], "none", "no_data", [], today)
        else:
            view[sid] = _series_view(sid, entry["status"], "last_known", "last_known",
                                     last["observations"], today)
    stale = [sid for sid, v in view.items() if v["stale"]]
    if stale:
        logger.info(f"FRED view: stale {stale}")
    return view
