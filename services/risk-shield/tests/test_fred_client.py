"""Part 3.2 — monitors/fred_client.py against respx. No socket, no real
limiter wait (a counting limiter is injected), today frozen."""

import asyncio
import logging
from datetime import date

import httpx
import pytest
import respx

from monitors import fred_client
from monitors.errors import (
    FredError,
    FredNotAuthorized,
    FredNotConfigured,
    FredRateLimited,
    FredSourceWide,
)
from monitors.fred_client import FredClient

KEY = "k3ysecretk3ysecretk3ysecret00001"
URL = "https://api.stlouisfed.org/fred/series/observations"
OK_BODY = {
    "count": 1,
    "observations": [
        {"realtime_start": "2026-09-10", "realtime_end": "2026-09-10",
         "date": "2026-09-08", "value": "4.12"},
    ],
}


class CountingLimiter:
    def __init__(self):
        self.n = 0

    async def acquire(self):
        self.n += 1
        return 0.0


def _client(key=KEY, **kw):
    return FredClient(key, limiter=CountingLimiter(), today=lambda: date(2026, 9, 10), **kw)


async def _raise_for(response=None, side_effect=None, key=KEY):
    with respx.mock(assert_all_called=False) as m:
        route = m.get(URL)
        if side_effect is not None:
            route.mock(side_effect=side_effect)
        else:
            route.mock(return_value=response)
        client = _client(key)
        try:
            with pytest.raises(FredError) as exc:
                await client.observations("DGS10")
        finally:
            await client.aclose()
        return exc.value, route


@pytest.mark.asyncio
async def test_fred_client_unconfigured_no_http():
    with respx.mock(assert_all_called=False) as m:
        route = m.get(URL).mock(return_value=httpx.Response(200, json=OK_BODY))
        client = _client("")
        with pytest.raises(FredNotConfigured):
            await client.observations("DGS10")
        await client.aclose()
    assert route.call_count == 0
    assert client.limiter.n == 0


@pytest.mark.asyncio
async def test_fred_client_429_rate_limited():
    err, _ = await _raise_for(httpx.Response(
        429, json={"error_code": 429, "error_message": "Too Many Requests.  Exceeded Rate Limit"}))
    assert isinstance(err, FredRateLimited)


@pytest.mark.asyncio
async def test_fred_client_423_locked_is_rate_limited():
    err, _ = await _raise_for(httpx.Response(423, json={"error_code": 423, "error_message": "Locked"}))
    assert isinstance(err, FredRateLimited)


@pytest.mark.asyncio
async def test_fred_client_400_api_key_not_authorized():
    err, _ = await _raise_for(httpx.Response(400, json={
        "error_code": 400,
        "error_message": "Bad Request.  The value for variable api_key is not registered.",
    }))
    assert isinstance(err, FredNotAuthorized)
    assert "api_key is not registered" not in str(err)   # inspected, never echoed


@pytest.mark.asyncio
async def test_fred_client_400_other_is_error():
    err, _ = await _raise_for(httpx.Response(
        400, json={"error_code": 400, "error_message": "Bad Request.  The series does not exist."}))
    assert type(err) is FredError
    assert not isinstance(err, FredSourceWide)


@pytest.mark.asyncio
async def test_fred_client_5xx_is_error():
    err, _ = await _raise_for(httpx.Response(500, text="Internal Server Error"))
    assert type(err) is FredError
    assert "HTTP 500" in str(err)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError(f"connect failed: {URL}?api_key={KEY}"), OSError(f"socket {KEY}")],
    ids=["httpx_connect_error", "oserror"],
)
async def test_fred_client_transport_error_is_typed(exc):
    err, _ = await _raise_for(side_effect=exc)
    assert type(err) is FredError
    assert KEY not in str(err) and KEY not in repr(err)
    assert err.__cause__ is None and err.__suppress_context__


@pytest.mark.asyncio
async def test_fred_client_hard_timeout_is_typed(monkeypatch):
    """httpx bounds each phase, not the request: the wait_for is the hard
    bound. A slow fake transport proves it fires and maps to FredError."""
    monkeypatch.setattr(fred_client, "FRED_REQUEST_TIMEOUT", 0.05)

    class SlowHTTP:
        async def get(self, url, params=None):
            await asyncio.sleep(1.0)

        async def aclose(self):
            return None

    client = _client(http=SlowHTTP())
    with pytest.raises(FredError, match="DGS10: timeout") as exc:
        await client.observations("DGS10")
    assert type(exc.value) is FredError
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


@pytest.mark.asyncio
async def test_fred_client_bad_json_is_error():
    err, _ = await _raise_for(httpx.Response(200, content=b"<html>not json</html>"))
    assert "not JSON" in str(err)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"error": "x"}, {"observations": "nope"}, [1, 2]])
async def test_fred_client_missing_observations_is_error(body):
    err, _ = await _raise_for(httpx.Response(200, json=body))
    assert type(err) is FredError
    assert "observations" in str(err)


@pytest.mark.asyncio
async def test_fred_client_key_never_in_logs_or_errors(caplog):
    scenarios = [
        dict(response=httpx.Response(429, json={"error_code": 429, "error_message": "x"})),
        dict(response=httpx.Response(400, json={"error_code": 400, "error_message": "api_key bad"})),
        dict(response=httpx.Response(500, text=f"echo {KEY}")),
        dict(side_effect=httpx.ConnectError(f"{URL}?api_key={KEY}")),
    ]
    errors = []
    with caplog.at_level(logging.DEBUG):
        for s in scenarios:
            err, _ = await _raise_for(**s)
            errors.append(err)
        with respx.mock() as m:
            m.get(URL).mock(return_value=httpx.Response(200, json=OK_BODY))
            client = _client()
            await client.observations("DGS10")
            await client.aclose()
    for err in errors:
        assert KEY not in str(err) and KEY not in repr(err)
        assert err.__cause__ is None
    for rec in caplog.records:
        assert KEY not in rec.getMessage()
    assert logging.getLogger("httpx").level == logging.WARNING


@pytest.mark.asyncio
async def test_fred_client_request_params_and_limiter():
    with respx.mock() as m:
        route = m.get(URL).mock(return_value=httpx.Response(200, json=OK_BODY))
        client = _client()
        body = await client.observations("DGS10")
        await client.aclose()
    assert body == OK_BODY
    params = route.calls.last.request.url.params
    assert params["series_id"] == "DGS10"
    assert params["file_type"] == "json"
    assert params["observation_start"] == "2024-07-02"   # 2026-09-10 − 800 days
    assert params["sort_order"] == "asc"
    assert params["api_key"] == KEY
    assert client._http.timeout == httpx.Timeout(8.0)
    assert fred_client.FRED_REQUEST_TIMEOUT == 8.0
    assert client.limiter.n == 1
    assert client.calls_made == 1
