"""
TradingFirm — FRED HTTP client (Part 3.2).

Thin, typed, no retries. One request per series:
GET https://api.stlouisfed.org/fred/series/observations

KEY HANDLING (docs/specs/3.2.md decision 7, approved as the second explicit
exception to G14 "secrets never in URLs", on the Alpha Vantage conditions).
FRED accepts the key only as the `api_key` query parameter, so it
unavoidably ends up in the request URL. Two conditions make that safe and
both live in this module:

  1. The httpx logger is pinned to WARNING here. At INFO httpx logs the
     full request line, key included.
  2. Typed errors never chain or format the original httpx exception or the
     response body: every raise uses `from None` and a message built from
     the series id and an HTTP status (or "timeout") only. The body's
     error_message is inspected for "api_key", never echoed.

Nothing in this module logs or formats `resp.url`.

Limits (fred.stlouisfed.org/docs/api/fred/errors.html, verified 2026-09-10):
120 requests/minute, then 429; ignoring 429s risks a temporary block. The
module-level ratelimit.fred_limiter runs at half that with a 1 s gap.

Timeout: FRED_REQUEST_TIMEOUT (8 s, data-engine's per-source bound) twice —
as the httpx client timeout, which bounds each phase separately, and as an
asyncio.wait_for around the whole request, which is the hard bound. Read at
call time so tests can patch it down.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from monitors.errors import (
    FredError,
    FredNotAuthorized,
    FredNotConfigured,
    FredRateLimited,
)
from ratelimit import RateLimiter, fred_limiter

logger = logging.getLogger(__name__)

# Condition 1 of decision 7: httpx logs the full URL (key included) at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
FRED_REQUEST_TIMEOUT = 8.0
# Two years plus the CPI release lag, so 3.3 can take YoY and its trend.
FRED_LOOKBACK_DAYS = 800


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


class FredClient:
    def __init__(
        self,
        api_key: str,
        *,
        limiter: RateLimiter | None = None,
        http: Any = None,
        timeout: float = FRED_REQUEST_TIMEOUT,
        base_url: str = FRED_BASE_URL,
        today: Callable[[], date] = _utc_today,
    ):
        self.api_key = (api_key or "").strip()
        self.limiter = limiter or fred_limiter
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self._today = today
        self.calls_made = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def observation_start(self) -> str:
        return (self._today() - timedelta(days=FRED_LOOKBACK_DAYS)).isoformat()

    async def observations(self, series_id: str) -> dict:
        """
        Raw observations body for one series (already canonical and
        allowlisted by monitors.fred). Raises one of the typed errors in
        monitors.errors; never retries.
        """
        where = series_id
        if not self.configured:
            raise FredNotConfigured(f"{where}: FRED_API_KEY is not set")

        waited = await self.limiter.acquire()
        if waited > 0:
            logger.debug(f"FRED limiter waited {waited:.2f}s before {where}")

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": self.observation_start(),
            "sort_order": "asc",
        }
        try:
            resp = await asyncio.wait_for(
                self._http.get(f"{self.base_url}/series/observations", params=params),
                timeout=FRED_REQUEST_TIMEOUT,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            # First: asyncio.TimeoutError is TimeoutError, an OSError.
            raise FredError(f"{where}: timeout") from None
        except (httpx.HTTPError, OSError) as e:
            # `from None`: httpx exception reprs embed the URL, key included.
            raise FredError(f"{where}: transport error: {type(e).__name__}") from None
        finally:
            self.calls_made += 1

        status = resp.status_code
        if status in (429, 423):
            raise FredRateLimited(f"{where}: HTTP {status}") from None
        if status == 400 and _names_api_key(resp):
            raise FredNotAuthorized(f"{where}: HTTP 400 (api_key rejected)") from None
        if status >= 400:
            raise FredError(f"{where}: HTTP {status}") from None
        try:
            body = resp.json()
        except ValueError:
            raise FredError(f"{where}: response is not JSON") from None
        if not isinstance(body, dict) or not isinstance(body.get("observations"), list):
            raise FredError(f"{where}: response has no observations list") from None
        return body

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()


def _names_api_key(resp: httpx.Response) -> bool:
    """Whether a 400 body blames the key. Inspected, never formatted."""
    try:
        body = resp.json()
    except ValueError:
        return False
    message = body.get("error_message") if isinstance(body, dict) else None
    return isinstance(message, str) and "api_key" in message.lower()
