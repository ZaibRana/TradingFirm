"""
TradingFirm — Alpha Vantage HTTP client (Part 2.3, fallback source only).

Thin, typed, no retries. Used only when yfinance has no past earnings
dates for a ticker; the primary path never touches this module.

KEY HANDLING (docs/specs/2.3.md decision 13, approved as an explicit
exception to G14 "secrets never in URLs"). Alpha Vantage accepts the key
only as the `apikey=` query parameter — there is no header form — so the
key unavoidably ends up in the request URL. Two conditions make that safe
and both live in this module:

  1. The httpx logger is pinned to WARNING here. At INFO httpx logs the
     full request line, key included, into the service log.
  2. Typed errors never chain or format the original httpx exception:
     httpx.HTTPStatusError.__str__ embeds the URL. Every raise uses
     `from None` and a message built from `function` + `symbol` only.

Nothing in this module logs or formats `resp.url`, and the recorder that
writes fixtures prints shape only.

Rate limits (free tier): 5 requests/minute, 25/day. The per-minute cap is
enforced in-process by ratelimit.alphavantage_limiter. The daily cap is
NOT a 429 — Alpha Vantage answers HTTP 200 with an {"Information": ...}
body — so it is detected by body shape and raised as AlphaVantageCapped.

Errors (all subclass AlphaVantageError):
    AlphaVantageNotConfigured  key empty — raised before any HTTP
    AlphaVantageRateLimited    {"Note": ...} — per-minute cap
    AlphaVantageCapped         {"Information": ...} — daily cap, HTTP 200
    AlphaVantageError          {"Error Message": ...}, non-2xx, transport,
                               bad JSON
"""

import logging
from typing import Any

import httpx

from providers.context.ratelimit import RateLimiter, alphavantage_limiter

logger = logging.getLogger(__name__)

# Condition (a) of decision 13: httpx logs the full URL (key included) at
# INFO. Pin it to WARNING for the whole process before any request is made.
logging.getLogger("httpx").setLevel(logging.WARNING)

ALPHAVANTAGE_BASE_URL = "https://www.alphavantage.co"
DEFAULT_TIMEOUT = 10.0


class AlphaVantageError(Exception):
    """Base class for every Alpha Vantage failure."""


class AlphaVantageNotConfigured(AlphaVantageError):
    """ALPHAVANTAGE_API_KEY is empty; no request was attempted."""


class AlphaVantageRateLimited(AlphaVantageError):
    """Per-minute cap ({"Note": ...}): stop now, do not retry."""


class AlphaVantageCapped(AlphaVantageError):
    """Daily cap: HTTP 200 with an {"Information": ...} body."""


class AlphaVantageClient:
    def __init__(
        self,
        api_key: str,
        *,
        limiter: RateLimiter | None = None,
        http: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        base_url: str = ALPHAVANTAGE_BASE_URL,
    ):
        self.api_key = (api_key or "").strip()
        self.limiter = limiter or alphavantage_limiter
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self.calls_made = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def get(self, function: str, symbol: str) -> Any:
        """
        GET /query?function=<function>&symbol=<symbol>. Returns the decoded
        JSON. Raises one of the typed errors above; never retries.

        Every error message names `function` and `symbol` only — never the
        URL, never the upstream exception (decision 13b).
        """
        where = f"{function}/{symbol}"
        if not self.configured:
            raise AlphaVantageNotConfigured(f"{where}: ALPHAVANTAGE_API_KEY is not set")

        waited = await self.limiter.acquire()
        if waited > 0:
            logger.debug(f"Alpha Vantage limiter waited {waited:.2f}s before {where}")

        try:
            resp = await self._http.get(
                f"{self.base_url}/query",
                params={"function": function, "symbol": symbol, "apikey": self.api_key},
            )
        except httpx.HTTPError as e:
            # `from None`: HTTPStatusError/RequestError reprs embed the URL.
            raise AlphaVantageError(f"{where}: transport error: {type(e).__name__}") from None
        finally:
            self.calls_made += 1

        if resp.status_code >= 400:
            raise AlphaVantageError(f"{where}: HTTP {resp.status_code}") from None
        try:
            body = resp.json()
        except ValueError:
            raise AlphaVantageError(f"{where}: response is not JSON") from None

        if isinstance(body, dict):
            # Order matters: the daily cap and the per-minute cap both come
            # back as HTTP 200 with an advisory body, never as a 429.
            if "Information" in body:
                raise AlphaVantageCapped(f"{where}: daily cap reached") from None
            if "Note" in body:
                raise AlphaVantageRateLimited(f"{where}: per-minute cap") from None
            if "Error Message" in body:
                raise AlphaVantageError(f"{where}: rejected by the API") from None
        return body

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
