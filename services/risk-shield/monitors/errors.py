"""
TradingFirm — Risk Shield typed source errors (Part 3.2).

Refusals raise, answers return an envelope (spec 3.2 decision 3). The FRED
hierarchy carries one extra level, FredSourceWide, because fred_snapshot
branches on it (decision 8): a state that is true for the whole source
stops the walk, a per-series FredError does not.

Messages are built from a series id / source name and an HTTP status only —
never a URL, never an upstream exception (the FRED key rides in the query
string; decision 7).
"""


class SourceError(Exception):
    """Base for every market-data source failure in risk-shield."""


# ── FRED ─────────────────────────────────────────────────────────

class FredError(SourceError):
    """Per-series failure: non-2xx (not a refusal), timeout, transport,
    bad JSON, wrong shape. The snapshot records it and moves on."""


class FredSourceWide(FredError):
    """A state that holds for every series. The snapshot stops on it."""


class FredNotConfigured(FredSourceWide):
    """FRED_API_KEY is empty; no request was attempted."""


class FredRateLimited(FredSourceWide):
    """429 or 423: stop, start the cooldown, never retry."""


class FredNotAuthorized(FredSourceWide):
    """400 whose error_message names api_key."""


class FredCoolingDown(FredSourceWide):
    """The FRED cooldown is active; no request was attempted."""

    def __init__(self, remaining: int):
        self.remaining = remaining
        super().__init__(f"fred cooling down, {remaining}s left")


# ── yfinance quotes ──────────────────────────────────────────────

class QuotesError(SourceError):
    """The core-quotes download failed for a reason that is not a refusal."""


class QuotesRateLimited(QuotesError):
    """yfinance rate limited us (raised or seen in its log)."""


class QuotesCoolingDown(QuotesError):
    """The yfinance cooldown is active; no download was attempted."""

    def __init__(self, remaining: int):
        self.remaining = remaining
        super().__init__(f"yfinance cooling down, {remaining}s left")


# ── Finnhub market news (Part 3.5) ───────────────────────────────
# The key rides in a header, but messages still carry a status only.

class FinnhubError(SourceError):
    """Non-2xx that is not a refusal, timeout, transport, bad JSON, or a
    body that is not a list."""


class FinnhubNotConfigured(FinnhubError):
    """FINNHUB_API_KEY is empty; no request was attempted."""


class FinnhubRateLimited(FinnhubError):
    """429: account-level. Start the cooldown, never retry."""


class FinnhubNotAuthorized(FinnhubError):
    """401 or 403: rejected key, or an endpoint outside the free tier."""
