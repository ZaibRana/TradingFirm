"""
TradingFirm — Finnhub HTTP client for risk-shield (Part 3.5).

Thin, typed, no retries. One request:
GET https://finnhub.io/api/v1/news?category=general

The pattern of data-engine's providers/context/finnhub_client.py, copied
(no shared package, spec 3.5 decision 4). The key goes in the
`X-Finnhub-Token` header, never the URL, so no G14 exception is needed.
Typed errors still carry a status (or "timeout") only and are raised
`from None`: nothing from the response or the httpx exception is echoed.

Limits: Finnhub's free tier allows 60 calls/min, then 429. The module-level
ratelimit.finnhub_limiter enforces 60 per 60 s plus a 1.2 s gap. A 429 is
account-level; data-engine holds the same key, which is why the poller also
reads data-engine's cooldown before calling this client.

Timeout: FINNHUB_REQUEST_TIMEOUT as the httpx client timeout and as an
asyncio.wait_for around the whole request (the hard bound). Read at call
time so tests can patch it down.
"""

import asyncio
import logging
from typing import Any

import httpx

from monitors.errors import (
    FinnhubError,
    FinnhubNotAuthorized,
    FinnhubNotConfigured,
    FinnhubRateLimited,
)
from ratelimit import RateLimiter, finnhub_limiter

logger = logging.getLogger(__name__)

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
FINNHUB_REQUEST_TIMEOUT = 8.0
NEWS_CATEGORY = "general"


class FinnhubClient:
    def __init__(
        self,
        api_key: str,
        *,
        limiter: RateLimiter | None = None,
        http: Any = None,
        timeout: float = FINNHUB_REQUEST_TIMEOUT,
        base_url: str = FINNHUB_BASE_URL,
    ):
        self.api_key = (api_key or "").strip()
        self.limiter = limiter or finnhub_limiter
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self.calls_made = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def general_news(self) -> list:
        """
        The raw general-news page: a list of Finnhub items, newest first.
        No `minId` (spec 3.5 decision 3): Finnhub's ids follow pickup order,
        not publish time, and a 100-item page spans ~41 h, so every poll
        takes the whole page and ingest dedups. Raises one of the typed
        errors in monitors.errors; never retries.
        """
        where = "general news"
        if not self.configured:
            raise FinnhubNotConfigured(f"{where}: FINNHUB_API_KEY is not set")

        waited = await self.limiter.acquire()
        if waited > 0:
            logger.debug(f"Finnhub limiter waited {waited:.2f}s before {where}")

        try:
            resp = await asyncio.wait_for(
                self._http.get(
                    f"{self.base_url}/news",
                    params={"category": NEWS_CATEGORY},
                    headers={"X-Finnhub-Token": self.api_key},
                ),
                timeout=FINNHUB_REQUEST_TIMEOUT,
            )
        except (asyncio.TimeoutError, httpx.TimeoutException):
            # First: asyncio.TimeoutError is TimeoutError, an OSError.
            raise FinnhubError(f"{where}: timeout") from None
        except (httpx.HTTPError, OSError) as e:
            raise FinnhubError(f"{where}: transport error: {type(e).__name__}") from None
        finally:
            self.calls_made += 1

        status = resp.status_code
        if status == 429:
            raise FinnhubRateLimited(f"{where}: HTTP 429") from None
        if status in (401, 403):
            raise FinnhubNotAuthorized(f"{where}: HTTP {status}") from None
        if not 200 <= status < 300:
            # 3xx included: redirects are not followed, and a redirect body
            # is not the news list.
            raise FinnhubError(f"{where}: HTTP {status}") from None
        try:
            body = resp.json()
        except ValueError:
            raise FinnhubError(f"{where}: response is not JSON") from None
        if not isinstance(body, list):
            raise FinnhubError(f"{where}: response is not a list") from None
        return body

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
