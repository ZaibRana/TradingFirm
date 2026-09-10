"""
TradingFirm — the macro brief inputs (Part 3.6a, spec decisions 5–6).

assemble_inputs() gathers what the brief reads into one bounded document:
health, settle, news, calendar and FRED, each with its own status, plus a
`freshness` block. 3.6b stores the document unchanged as
risk.macro_briefs.inputs and sends it to ai-agent; GET /macro/brief/inputs
serves it so the inputs can be checked without an LLM.

Health comes from risk.health_checks rows only: this module never computes a
check or downloads quotes.

Each section has a *_freshness() fragment; the fragments merge into
`freshness`, so a test on one section asserts exactly what the brief sees.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import db
import scheduler

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# ── Health + settle (decision 5) ─────────────────────────────────

# One slot of grace: the check the scheduler should already have recorded is
# the latest slot starting at or before now − this. A check takes ~5 s cold.
SLOT_GRACE_SECONDS = 300

HEALTH_STATUSES = ("ok", "no_checks", "unavailable")


def _indicators(row: dict) -> dict:
    """The row's indicators JSONB as a dict; {} when it is not an object
    (asyncpg returns jsonb as text without a codec)."""
    value = row.get("indicators")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _monitors(value: Any) -> Optional[dict]:
    """{name: {score, weight, stale, detail}}, without the raw series; None
    when the stored shape is wrong."""
    if not isinstance(value, dict) or not all(isinstance(m, dict) for m in value.values()):
        return None
    return {
        name: {"score": m.get("score"), "weight": m.get("weight"),
               "stale": m.get("stale"), "detail": m.get("detail")}
        for name, m in value.items()
    }


def _settle_part(settle: Optional[dict]) -> dict:
    if settle is None:
        return {"present": False, "checkedAt": None, "score": None}
    return {"present": True, "checkedAt": settle["checkedAt"].isoformat(), "score": settle["score"]}


async def health_section(pool, now: datetime) -> tuple[dict, dict]:
    """
    (health, settle). Health is the latest risk.health_checks row; when its
    score is null, lastScored is the latest scored row (as /market/health).
    Settle is the latest scored settle before `now`. No pool or a database
    failure is status "unavailable"; no row at all is "no_checks". Never raises
    for a dependency state.
    """
    expected = scheduler.last_slot_before(now - timedelta(seconds=SLOT_GRACE_SECONDS))
    expected_at = expected[1].isoformat() if expected is not None else None

    if pool is None:
        return {"status": "unavailable", "lastExpectedSlotAt": expected_at}, _settle_part(None)
    try:
        row = await db.latest_health_check(pool)
        settle = await db.settle_base(pool, now)
        scored = None
        if row is not None and row["score"] is None:
            scored = await db.latest_scored_health_check(pool)
    except db.DB_FAILURES as e:
        logger.warning(f"Macro inputs: health read failed ({type(e).__name__})")
        return {"status": "unavailable", "lastExpectedSlotAt": expected_at}, _settle_part(None)

    if row is None:
        return {"status": "no_checks", "lastExpectedSlotAt": expected_at}, _settle_part(settle)

    ind = _indicators(row)
    checked_at = row["checked_at"]
    health = {
        "status": "ok",
        "checkedAt": checked_at.isoformat(),
        "kind": ind.get("kind"),
        "score": row["score"],
        "regime": row["regime"],
        "trend": row["trend"],
        "coverage": ind.get("coverage"),
        "stale": ind.get("stale"),
        "staleMonitors": ind.get("staleMonitors"),
        "monitors": _monitors(ind.get("monitors")),
        "ageMinutes": int((now - checked_at).total_seconds() // 60),
        "lastExpectedSlotAt": expected_at,
    }
    if row["score"] is None:
        health["lastScored"] = (
            {"score": scored["score"], "regime": scored["regime"],
             "checkedAt": scored["checked_at"].isoformat()}
            if scored is not None else None
        )
    return health, _settle_part(settle)


def health_ready(health: dict) -> bool:
    """3.6b's precondition to spend an LLM call: the database answered and a
    scored row exists (the latest, or lastScored)."""
    if health.get("status") != "ok":
        return False
    return health.get("score") is not None or health.get("lastScored") is not None


def health_freshness(health: dict) -> dict:
    """The health fragment of `freshness`. healthStale: no usable row, or the
    row is older than the slot that should already have produced one. The age
    is carried as-is, so a fresh 07:30 verdict still shows ~670 minutes."""
    ok = health.get("status") == "ok"
    expected = health.get("lastExpectedSlotAt")
    behind = not ok or (
        expected is not None
        and datetime.fromisoformat(health["checkedAt"]) < datetime.fromisoformat(expected)
    )
    return {
        "healthStatus": health.get("status"),
        "healthStale": behind,
        "healthMonitorsStale": bool(health.get("stale")) if ok else False,
        "healthAgeMinutes": health.get("ageMinutes"),
    }
