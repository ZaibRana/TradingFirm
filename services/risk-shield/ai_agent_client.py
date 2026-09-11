"""
TradingFirm — the ai-agent brief client (Part 3.6b, spec decision 1).

One POST {AI_AGENT_URL}/brief/macro with {"inputs": <macro inputs document>}.
The answer must meet the contract below or it is rejected, never truncated or
repaired: an LLM answer that breaks it is ai-agent's bug, and a cut paragraph
would store a brief that says less than it seems to. The one normalization: a
blank or whitespace-only `model` is returned as null. No retries, no cooldown,
no backoff (the callers' cadence bounds the calls), no auth header (ai-agent
has none).

The 16,000-byte bound is measured on compact JSON:
`json.dumps(body, allow_nan=False, separators=(",", ":"))` with the default
`ensure_ascii=True`, encoded as UTF-8, the same measure as
`macro_inputs.encoded_size`. Every non-ASCII character counts as its JSON
escape: 6 bytes, or 12 for an emoji.

  answer                                  error               log
  200, meets the contract                 none                INFO, the lengths
  200, breaks it / not JSON / not object  AiAgentBadResponse  ERROR once per rule, then DEBUG
  404 (today's scaffold has no route)     AiAgentUnavailable  WARNING every time
  422 (our inputs contract drifted)       AiAgentRejected     ERROR
  429, other 4xx, 5xx, 3xx                AiAgentUnavailable  WARNING, the status
  transport error, timeout                AiAgentUnavailable  WARNING, the type

The limits exist twice: BRIEF_LIMITS here, pinned by
test_brief_limits_pinned_to_spec, and a pinned copy in ai-agent (Part 4.6,
decisions.md "Part 4.6's brief contract is camelCase"). Change both or neither.
"""

import asyncio
import json
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

BRIEF_PATH = "/brief/macro"
BRIEF_TIMEOUT = 180.0     # provisional (4.6 revisits it): hard bound per call, read at call time

# A text field: max chars, non-blank. A list field: (min items, max items, max
# chars per non-blank item). `model` is the one optional field, blank allowed.
# "bytes" bounds the whole object (the measure is in the module docstring).
BRIEF_LIMITS = {
    "regimeView": 1_000,
    "keyRisks": (1, 8, 300),
    "upcoming": (0, 10, 300),
    "oneParagraph": 2_000,
    "model": 100,
    "bytes": 16_000,
}
BRIEF_FIELDS = ("regimeView", "keyRisks", "upcoming", "oneParagraph", "model")


class AiAgentError(Exception):
    """Base. Messages carry a status, a type or a contract rule: never a body or a URL."""


class AiAgentUnavailable(AiAgentError):
    """No usable answer: 404, 429, another 4xx, 5xx, 3xx, a transport error, a timeout."""


class AiAgentRejected(AiAgentError):
    """422: ai-agent refused the inputs document."""


class AiAgentBadResponse(AiAgentError):
    """200 with a body that is not JSON, not an object, or outside BRIEF_LIMITS."""


def _text_problem(value: Any, max_chars: int, *, blank_ok: bool = False) -> Optional[tuple[str, str]]:
    if not isinstance(value, str):
        return "not a string", "not a string"
    if not blank_ok and not value.strip():
        return "blank", "blank"
    if len(value) > max_chars:
        return "too long", f"{len(value)} chars"
    return None


def contract_problem(body: Any) -> Optional[tuple[str, str]]:
    """(rule, detail) for the first rule `body` breaks, None when it meets the
    contract. The rule carries no measured value, so the ERROR-once set is no
    larger than the rule list; the detail says how it broke."""
    if not isinstance(body, dict):
        return "not an object", "not an object"
    try:
        size = len(json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    except ValueError:          # json.loads accepts NaN, so the dump is the check
        return "NaN", "NaN or Infinity"
    if size > BRIEF_LIMITS["bytes"]:
        return "too large", f"{size} bytes"
    extra = [key for key in body if key not in BRIEF_FIELDS]
    if extra:
        return "unexpected key", f"{len(extra)} unexpected key(s)"
    for field in BRIEF_FIELDS:
        if field not in body:
            if field == "model":
                continue
            return f"{field}: missing", f"{field}: missing"
        value, limit, name = body[field], BRIEF_LIMITS[field], field
        if not isinstance(limit, tuple):
            problem = _text_problem(value, limit, blank_ok=field == "model")
        elif not isinstance(value, list):
            problem = ("not a list", "not a list")
        elif not limit[0] <= len(value) <= limit[1]:
            problem = ("too few items" if len(value) < limit[0] else "too many items", f"{len(value)} items")
        else:
            problem = next(filter(None, (_text_problem(item, limit[2]) for item in value)), None)
            name = f"{field} item"
        if problem:
            return f"{name}: {problem[0]}", f"{name}: {problem[1]}"
    return None


# A bad answer is ai-agent's bug: ERROR once per rule, then DEBUG, so a brief
# every few hours does not bury the first report (macro_inputs' pattern).
_bad_response_logged: set[str] = set()


class AiAgentClient:
    """One httpx.AsyncClient, redirects never followed. `transport` is for
    tests and the twin round trip (an httpx.MockTransport)."""

    def __init__(self, base_url: str, *, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.url = f"{base_url.rstrip('/')}{BRIEF_PATH}"
        self._http = httpx.AsyncClient(transport=transport, timeout=BRIEF_TIMEOUT, follow_redirects=False)

    async def brief(self, inputs: dict) -> dict:
        """The validated answer to one POST, or a typed error. Cancellation propagates."""
        try:
            resp = await asyncio.wait_for(self._http.post(self.url, json={"inputs": inputs}),
                                          timeout=BRIEF_TIMEOUT)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            # First: asyncio.TimeoutError is TimeoutError, an OSError.
            logger.warning("ai-agent /brief/macro timed out")
            raise AiAgentUnavailable("timeout") from None
        except (httpx.HTTPError, OSError) as e:
            logger.warning(f"ai-agent /brief/macro unreachable ({type(e).__name__})")
            raise AiAgentUnavailable(type(e).__name__) from None

        status = resp.status_code
        if status == 404:
            # Every time: nothing may hide the first call after 4.6 ships (decision 1).
            logger.warning("ai-agent has no /brief/macro (HTTP 404)")
            raise AiAgentUnavailable("HTTP 404 (no /brief/macro)")
        if status == 422:
            logger.error("ai-agent rejected the inputs document (HTTP 422): the inputs contract has drifted")
            raise AiAgentRejected("HTTP 422")
        if status != 200:
            logger.warning(f"ai-agent /brief/macro answered HTTP {status}")
            raise AiAgentUnavailable(f"HTTP {status}")

        try:
            body = resp.json()
        except ValueError:
            problem = ("not JSON", "not JSON")
        else:
            problem = contract_problem(body)
        if problem is not None:
            rule, detail = problem
            if rule in _bad_response_logged:
                logger.debug(f"ai-agent /brief/macro answer breaks the brief contract again ({detail})")
            else:
                _bad_response_logged.add(rule)
                logger.error(f"ai-agent /brief/macro answer breaks the brief contract ({detail})")
            raise AiAgentBadResponse(detail)
        if isinstance(body.get("model"), str) and not body["model"].strip():
            body["model"] = None
        logger.info(f"ai-agent brief: regimeView {len(body['regimeView'])} chars, {len(body['keyRisks'])} key risks, "
                    f"{len(body['upcoming'])} upcoming, oneParagraph {len(body['oneParagraph'])} chars")
        return body

    async def aclose(self) -> None:
        await self._http.aclose()
