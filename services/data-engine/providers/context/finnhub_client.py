"""
TradingFirm — Finnhub HTTP client (Part 2.1).

Thin, typed, no retries. One instance per process, shared by the fetchers
in providers/context/finnhub.py. The API key goes in the `X-Finnhub-Token`
header, never in the URL, so it cannot land in a log line or a fixture.

Rate limits (Finnhub free tier: 60 calls/min, 30/s burst). G6: we enforce
60 per rolling 60 s *and* a 1.2 s minimum gap between calls, in-process.
A 429 raises `FinnhubRateLimited` immediately; the caller stops. There is
no retry anywhere in this module.

Errors (all subclass `FinnhubError`):
    FinnhubNotConfigured  key empty — raised before any HTTP
    FinnhubAuthError      401 / 403 (bad key, or premium endpoint)
    FinnhubRateLimited    429
    FinnhubError          any other non-2xx, transport error, bad JSON
"""

import logging
from typing import Any

import httpx

from providers.context.ratelimit import RateLimiter  # noqa: F401  (re-exported for callers and tests)

logger = logging.getLogger(__name__)

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_TIMEOUT = 10.0


class FinnhubError(Exception):
    """Base class for every Finnhub failure."""


class FinnhubNotConfigured(FinnhubError):
    """FINNHUB_API_KEY is empty; no request was attempted."""


class FinnhubAuthError(FinnhubError):
    """401 or 403: rejected key, or an endpoint outside the free tier."""


class FinnhubRateLimited(FinnhubError):
    """429: stop now, do not retry."""


class FinnhubClient:
    def __init__(
        self,
        api_key: str,
        *,
        limiter: RateLimiter | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        base_url: str = FINNHUB_BASE_URL,
    ):
        self.api_key = (api_key or "").strip()
        self.limiter = limiter or RateLimiter()
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self.calls_made = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def get(self, path: str, **params: Any) -> Any:
        """
        GET `path` (e.g. "/stock/profile2") with query params. Returns the
        decoded JSON. Raises one of the typed errors above; never retries.
        """
        if not self.configured:
            raise FinnhubNotConfigured("FINNHUB_API_KEY is not set")

        waited = await self.limiter.acquire()
        if waited > 0:
            logger.debug(f"Finnhub limiter waited {waited:.2f}s before {path}")

        url = f"{self.base_url}{path}"
        try:
            resp = await self._http.get(
                url, params=params, headers={"X-Finnhub-Token": self.api_key}
            )
        except (httpx.HTTPError, OSError) as e:
            # OSError as well as httpx's own errors: a bare socket error must
            # not escape the client. Part 2.4 keys "the database failed" on
            # OSError (db.DB_ERRORS), so an unmapped OSError from here would
            # turn an upstream hiccup into a 503 instead of a degraded section.
            raise FinnhubError(f"{path}: transport error: {type(e).__name__}: {e}") from e
        finally:
            self.calls_made += 1

        if resp.status_code == 429:
            raise FinnhubRateLimited(f"{path}: 429 rate limited")
        if resp.status_code in (401, 403):
            raise FinnhubAuthError(f"{path}: {resp.status_code} {resp.text[:120]}")
        if resp.status_code >= 400:
            raise FinnhubError(f"{path}: {resp.status_code} {resp.text[:120]}")
        try:
            return resp.json()
        except ValueError as e:
            raise FinnhubError(f"{path}: response is not JSON") from e

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
