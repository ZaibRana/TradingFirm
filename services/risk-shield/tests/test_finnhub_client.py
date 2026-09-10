"""Part 3.5 — monitors/finnhub_client.py against respx. No socket, no real
limiter wait (a counting limiter is injected)."""

import logging

import httpx
import pytest
import respx

import ratelimit
from monitors import finnhub_client
from monitors.errors import (
    FinnhubError,
    FinnhubNotAuthorized,
    FinnhubNotConfigured,
    FinnhubRateLimited,
)
from monitors.finnhub_client import FinnhubClient

KEY = "f1nnhubk3ysecretf1nnhub01"
URL = "https://finnhub.io/api/v1/news"
ITEM = {
    "category": "top news", "datetime": 1757500000, "headline": "Stocks drift",
    "id": 7512345, "image": "", "related": "", "source": "Reuters",
    "summary": "Markets were quiet.", "url": "https://example.com/a",
}


class CountingLimiter:
    def __init__(self):
        self.n = 0

    async def acquire(self):
        self.n += 1
        return 0.0


def _client(key=KEY):
    return FinnhubClient(key, limiter=CountingLimiter())


@pytest.mark.asyncio
async def test_finnhub_client_sends_key_in_header_only():
    with respx.mock() as m:
        route = m.get(URL).mock(return_value=httpx.Response(200, json=[ITEM]))
        client = _client()
        body = await client.general_news()
        await client.aclose()
    request = route.calls.last.request
    assert body == [ITEM]
    assert request.headers["X-Finnhub-Token"] == KEY
    assert KEY not in str(request.url)
    assert dict(request.url.params) == {"category": "general"}
    assert client.calls_made == 1
    assert client.limiter.n == 1


@pytest.mark.asyncio
async def test_finnhub_client_empty_page_is_an_answer():
    # The client returns [] as-is; deciding that an empty page is a problem
    # is the poller's job (spec 3.5 decision 7).
    with respx.mock() as m:
        route = m.get(URL).mock(return_value=httpx.Response(200, json=[]))
        client = _client()
        body = await client.general_news()
        await client.aclose()
    assert body == []
    assert dict(route.calls.last.request.url.params) == {"category": "general"}


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["", "   ", None])
async def test_finnhub_client_unconfigured_makes_no_request(key):
    with respx.mock(assert_all_called=False) as m:
        route = m.get(URL).mock(return_value=httpx.Response(200, json=[ITEM]))
        client = _client(key)
        with pytest.raises(FinnhubNotConfigured):
            await client.general_news()
        await client.aclose()
    assert route.call_count == 0
    assert client.limiter.n == 0
    assert client.calls_made == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("response, expected", [
    (httpx.Response(429, json={"error": "API limit reached"}), FinnhubRateLimited),
    (httpx.Response(401, json={"error": "Invalid API key"}), FinnhubNotAuthorized),
    (httpx.Response(403, json={"error": "You don't have access"}), FinnhubNotAuthorized),
    (httpx.Response(500, text="oops"), FinnhubError),
    (httpx.Response(302, headers={"location": "https://finnhub.io/"}), FinnhubError),
    (httpx.Response(200, text="<html>not json</html>"), FinnhubError),
    (httpx.Response(200, json={"error": "not a list"}), FinnhubError),
], ids=["429", "401", "403", "500", "302", "not-json", "not-a-list"])
async def test_finnhub_client_status_mapping(response, expected):
    with respx.mock() as m:
        m.get(URL).mock(return_value=response)
        client = _client()
        with pytest.raises(FinnhubError) as exc:
            await client.general_news()
        await client.aclose()
    assert type(exc.value) is expected
    assert client.calls_made == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("side_effect", [
    httpx.ReadTimeout(f"read timed out {KEY}"),
    httpx.ConnectError(f"connect failed {KEY}"),
    OSError(f"socket {KEY}"),
], ids=["timeout", "connect", "oserror"])
async def test_finnhub_client_errors_never_carry_key(side_effect, caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock() as m:
        m.get(URL).mock(side_effect=side_effect)
        client = _client()
        with pytest.raises(FinnhubError) as exc:
            await client.general_news()
        await client.aclose()
    err = exc.value
    assert type(err) is FinnhubError
    assert KEY not in str(err)
    assert err.__cause__ is None and err.__suppress_context__
    assert KEY not in caplog.text

    # A refusal whose body echoes the key: the message still carries a status only.
    with respx.mock() as m:
        m.get(URL).mock(return_value=httpx.Response(403, text=f"bad token {KEY}"))
        client = _client()
        with pytest.raises(FinnhubNotAuthorized) as exc:
            await client.general_news()
        await client.aclose()
    assert str(exc.value) == "general news: HTTP 403"


@pytest.mark.asyncio
async def test_finnhub_limiter_defaults():
    limiter = ratelimit.finnhub_limiter
    assert (limiter.max_calls, limiter.window, limiter.min_gap) == (60, 60.0, 1.2)
    assert (ratelimit.FINNHUB_MAX_CALLS_PER_MINUTE, ratelimit.FINNHUB_MIN_GAP) == (60, 1.2)
    client = FinnhubClient(KEY)
    try:
        assert client.limiter is ratelimit.finnhub_limiter
        assert finnhub_client.FINNHUB_REQUEST_TIMEOUT == 8.0
    finally:
        await client.aclose()
