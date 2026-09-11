"""
TradingFirm — macro brief generation (Part 3.6b).

generate_once (spec decision 2) is one generation: busy → no pool → inputs →
ready → ai-agent → insert → brief_status. A row is stored only for a valid
ai-agent answer on ready inputs, and nothing retries: the next slot, regime
request or manual call is independent. Every function takes a `clock`;
nothing reads the time directly.

brief_status is process memory and small (G8): the inputs document is never
kept in state.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import db
import macro_inputs
from ai_agent_client import AiAgentBadResponse, AiAgentError

logger = logging.getLogger(__name__)

GENERATED, SKIPPED, FAILED = "generated", "skipped", "failed"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def initial_brief_status() -> dict:
    """app.state.brief_status before any attempt. lastBriefAt, lastBriefId and
    lastTrigger describe the last stored brief; lastError the last attempt."""
    return {"lastAttemptAt": None, "lastBriefAt": None, "lastBriefId": None, "lastTrigger": None,
            "lastError": None}


def health_regime_score(health: dict) -> tuple[Optional[str], Optional[int]]:
    """The inputs' latest regime and score, or lastScored's when the score is null."""
    if health.get("score") is not None:
        return health.get("regime"), health["score"]
    scored = health.get("lastScored") or {}
    return scored.get("regime"), scored.get("score")


def _not_stored(status: dict, outcome: str, cause: str) -> dict:
    status["lastError"] = cause
    return {"outcome": outcome, "cause": cause, "id": None, "row": None}


async def generate_once(state, client, *, trigger: str, clock: Callable[[], datetime] = _utc_now) -> dict:
    """
    One generation on `state` (brief_lock, brief_status, db_pool, fred_client,
    inputs_http, …) with an AiAgentClient. Returns {outcome, cause, id, row}:
    generated (row = the stored columns, shaped as db.latest_macro_brief reads
    them), skipped (busy / database unavailable / inputs not ready) or failed
    (ai-agent: … / insert: …). A dependency failure never raises; a bug does,
    for the loop (ERROR) or the endpoint (500). Cancellation propagates, and
    nothing is stored mid-call.
    """
    if state.brief_lock.locked():            # checked without waiting: never queue behind a 180 s call
        logger.info(f"Macro brief ({trigger}) skipped: a generation is running")
        return {"outcome": SKIPPED, "cause": "busy", "id": None, "row": None}
    async with state.brief_lock:
        status = state.brief_status
        status["lastAttemptAt"] = clock().isoformat()
        pool = getattr(state, "db_pool", None)
        if pool is None:                     # never spend an LLM call that cannot be stored
            logger.warning(f"Macro brief ({trigger}) skipped: database unavailable")
            return _not_stored(status, SKIPPED, "database unavailable")

        # Assembled directly, never GET /macro/brief/inputs' 60 s reuse.
        inputs = await macro_inputs.assemble_inputs(state, state.fred_client, state.inputs_http, now=clock())
        if not inputs["ready"]:
            logger.warning(f"Macro brief ({trigger}) skipped: inputs not ready "
                           f"(health {inputs['health'].get('status')}), no ai-agent call")
            return _not_stored(status, SKIPPED, "inputs not ready")

        try:
            answer = await client.brief(inputs)
        except AiAgentError as e:            # the client has logged it
            cause = f"AiAgentBadResponse ({e})" if isinstance(e, AiAgentBadResponse) else str(e)
            return _not_stored(status, FAILED, f"ai-agent: {cause}")

        regime, score = health_regime_score(inputs["health"])
        row = {"generated_at": clock(), "trigger": trigger, "regime": regime, "health_score": score,
               "brief_text": answer["oneParagraph"], "brief": answer, "inputs": inputs}
        try:
            row = {"id": await db.insert_macro_brief(pool, **row), **row}
        except db.DB_FAILURES as e:
            logger.warning(f"Macro brief lost ({trigger}): the insert failed ({type(e).__name__})")
            return _not_stored(status, FAILED, f"insert: {type(e).__name__}")

        status.update(lastBriefAt=row["generated_at"].isoformat(), lastBriefId=row["id"], lastTrigger=trigger,
                      lastError=None)
    logger.info(f"Macro brief ({trigger}) stored: {row['id']}, {regime} {score}")
    return {"outcome": GENERATED, "cause": None, "id": row["id"], "row": row}
