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
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from cache import (
    KIND_FRED,
    SOURCE_FRED,
    TTL_COOLDOWN_FRED,
    TTL_COOLDOWN_FRED_AUTH,
    TTL_DEGRADED,
    TTL_FRED,
    cached_json,
    canonical,
    cooldown_remaining,
    risk_key,
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
