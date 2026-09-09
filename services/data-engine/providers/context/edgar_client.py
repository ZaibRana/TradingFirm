"""
TradingFirm — SEC EDGAR HTTP client (Part 2.2).

Thin, typed, no retries. Two hosts: `www.sec.gov` serves the ticker → CIK
file, `data.sec.gov` serves the per-company submissions JSON. There is no
API key; EDGAR requires a declared User-Agent of the form
"<app name> <contact email>" (settings.edgar_user_agent) and refuses
undeclared clients with a 403. The header carries the contact only — it
never appears in a URL, a log line or a fixture.

Rate limit (SEC fair-access policy): at most 10 requests per second per
client. G6: enforced in-process by `ratelimit.edgar_limiter`, one rolling
window of 10 per second, module-level so every client in the process
shares it; tests inject their own (`EdgarClient(limiter=...)`). A 403 or
429 means "blocked or throttled" and raises `EdgarRateLimited`
immediately; the caller stops. There is no retry anywhere in this module.

Errors (all subclass `EdgarError`):
    EdgarNotConfigured  User-Agent empty — raised before any HTTP
    EdgarRateLimited    403 / 429 (undeclared client, or rate threshold)
    EdgarNotFound       404 (unknown CIK)
    EdgarError          any other non-2xx, transport error, bad JSON
"""

import logging
from typing import Any

import httpx

from providers.context.ratelimit import RateLimiter, edgar_limiter

logger = logging.getLogger(__name__)

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
EDGAR_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_TIMEOUT = 15.0  # submissions bodies run to ~1 MB for large issuers

def submissions_url(cik: int) -> str:
    """https://data.sec.gov/submissions/CIK##########.json (10-digit, zero-padded)."""
    return f"{EDGAR_SUBMISSIONS_BASE}/CIK{int(cik):010d}.json"


class EdgarError(Exception):
    """Base class for every EDGAR failure."""


class EdgarNotConfigured(EdgarError):
    """EDGAR_USER_AGENT is empty; no request was attempted."""


class EdgarRateLimited(EdgarError):
    """403 or 429: blocked or throttled. Stop now, do not retry."""


class EdgarNotFound(EdgarError):
    """404: the CIK has no submissions file."""


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        *,
        limiter: RateLimiter | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.user_agent = (user_agent or "").strip()
        self.limiter = limiter if limiter is not None else edgar_limiter
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self.calls_made = 0

    @property
    def configured(self) -> bool:
        return bool(self.user_agent)

    async def get_json(self, url: str) -> Any:
        """
        GET a full EDGAR URL and return the decoded JSON. Raises one of the
        typed errors above; never retries.
        """
        if not self.configured:
            raise EdgarNotConfigured("EDGAR_USER_AGENT is not set")

        waited = await self.limiter.acquire()
        if waited > 0:
            logger.debug(f"EDGAR limiter waited {waited:.2f}s before {url}")

        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        try:
            resp = await self._http.get(url, headers=headers)
        except (httpx.HTTPError, OSError) as e:
            # OSError too — see the note in finnhub_client.get(): an unmapped
            # OSError would read as a database failure in Part 2.4.
            raise EdgarError(f"{url}: transport error: {type(e).__name__}: {e}") from e
        finally:
            self.calls_made += 1

        if resp.status_code in (403, 429):
            raise EdgarRateLimited(f"{url}: {resp.status_code} blocked/throttled")
        if resp.status_code == 404:
            raise EdgarNotFound(f"{url}: 404")
        if resp.status_code >= 400:
            raise EdgarError(f"{url}: {resp.status_code} {resp.text[:120]}")
        try:
            return resp.json()
        except ValueError as e:
            raise EdgarError(f"{url}: response is not JSON") from e

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
