"""Part 3.6b — the ai-agent brief client (spec decision 1). ai-agent is
tests/fake_ai_agent.py's MockTransport; no socket."""

import json
import logging

import httpx
import pytest

import ai_agent_client
from ai_agent_client import AiAgentBadResponse, AiAgentRejected, AiAgentUnavailable
from tests.fake_ai_agent import AGENT_URL, DROP, FakeAiAgent, brief, contract_violations

LOGGER = "ai_agent_client"
INPUTS = {"schemaVersion": 1, "ready": True, "health": {"status": "ok", "score": 64},
          "news": {"status": "ok", "items": []}, "freshness": {"anyStale": False}}


@pytest.fixture(autouse=True)
def _error_once_reset():
    ai_agent_client._bad_response_logged.clear()


async def _call(agent):
    client = agent.client()
    try:
        return await client.brief(INPUTS)
    finally:
        await client.aclose()


def _size(body):
    return len(json.dumps(body, separators=(",", ":")).encode("utf-8"))


def _levels(caplog):
    return [r.levelname for r in caplog.records if r.name == LOGGER]


def test_brief_limits_pinned_to_spec():
    assert ai_agent_client.BRIEF_LIMITS == {
        "regimeView": 1000, "keyRisks": (1, 8, 300), "upcoming": (0, 10, 300),
        "oneParagraph": 2000, "model": 100, "bytes": 16000,
    }, "ai-agent's POST /brief/macro (4.6) keeps a pinned copy of these limits. Change both."
    assert ai_agent_client.BRIEF_FIELDS == ("regimeView", "keyRisks", "upcoming", "oneParagraph", "model")
    assert (ai_agent_client.BRIEF_PATH, ai_agent_client.BRIEF_TIMEOUT) == ("/brief/macro", 180)
    assert ai_agent_client.contract_problem(brief()) is None          # the fixture meets the contract


@pytest.mark.asyncio
async def test_ai_agent_client_posts_inputs_document():
    agent = FakeAiAgent()
    client = agent.client(AGENT_URL + "/")
    await client.brief(INPUTS)
    assert client._http.follow_redirects is False
    await client.aclose()
    (request,) = agent.requests
    assert (request.method, str(request.url)) == ("POST", f"{AGENT_URL}/brief/macro")
    assert json.loads(request.content) == {"inputs": INPUTS}         # the document as given, nothing added
    assert "authorization" not in request.headers


@pytest.mark.asyncio
async def test_ai_agent_client_valid_brief_returned(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for body in (brief(), brief(model=DROP)):
            assert await _call(FakeAiAgent((200, body))) == body
    b = brief()
    line = (f"ai-agent brief: regimeView {len(b['regimeView'])} chars, {len(b['keyRisks'])} key risks, "
            f"{len(b['upcoming'])} upcoming, oneParagraph {len(b['oneParagraph'])} chars")
    assert [(r.levelname, r.getMessage()) for r in caplog.records if r.name == LOGGER] == [("INFO", line)] * 2


@pytest.mark.asyncio
async def test_ai_agent_client_rejects_contract_violations(caplog):
    violations = contract_violations()
    expected = ["regimeView: missing", "oneParagraph: blank", "oneParagraph: 2001 chars", "keyRisks: 0 items",
                "keyRisks: 9 items", "keyRisks item: 301 chars", "1 unexpected key(s)", "not an object",
                "not JSON", "NaN or Infinity", f"{_size(violations['over 16 KB'])} bytes"]
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for _ in range(2):                                             # each problem twice
            for (name, body), detail in zip(violations.items(), expected):
                agent = FakeAiAgent((200, body))
                with pytest.raises(AiAgentBadResponse) as info:
                    await _call(agent)
                assert (str(info.value), len(agent.requests)) == (detail, 1), name
    assert _levels(caplog) == ["ERROR"] * 11 + ["DEBUG"] * 11          # one ERROR per distinct problem


@pytest.mark.asyncio
async def test_ai_agent_client_accepts_limits_exactly():
    at_limits = {"regimeView": "v" * 1000, "keyRisks": ["k" * 300] * 8, "upcoming": ["u" * 300] * 10,
                 "oneParagraph": "p" * 2000, "model": "m" * 100}
    fewest = brief(keyRisks=["one risk"], upcoming=[])
    exact = brief(oneParagraph="p")                                    # 16,000 bytes: 📈 is 12 escaped bytes
    pad = 16_000 - _size(exact)
    exact["oneParagraph"] += "\U0001F4C8" * (pad // 12) + "p" * (pad % 12)
    assert _size(exact) == 16_000 and len(exact["oneParagraph"]) <= 2000
    for body in (at_limits, fewest, exact):
        assert await _call(FakeAiAgent((200, body))) == body
    for blank in ("", " \n\t"):                                        # a blank model is normalized, not rejected
        assert await _call(FakeAiAgent((200, {**fewest, "model": blank}))) == {**fewest, "model": None}

    for over, detail in (({**at_limits, "regimeView": "v" * 1001}, "regimeView: 1001 chars"),
                         ({**at_limits, "upcoming": ["u"] * 11}, "upcoming: 11 items"),
                         ({**at_limits, "upcoming": ["u" * 301]}, "upcoming item: 301 chars"),
                         ({**at_limits, "model": "m" * 101}, "model: 101 chars"),
                         ({**at_limits, "model": None}, "model: not a string"),
                         ({**exact, "oneParagraph": exact["oneParagraph"] + "p"}, "16001 bytes")):
        with pytest.raises(AiAgentBadResponse, match=f"^{detail}$"):
            await _call(FakeAiAgent((200, over)))


@pytest.mark.asyncio
async def test_ai_agent_client_status_mapping(caplog):
    cases = [(404, AiAgentUnavailable, "HTTP 404 (no /brief/macro)", "WARNING"),
             (404, AiAgentUnavailable, "HTTP 404 (no /brief/macro)", "WARNING"),     # every time, never once
             (422, AiAgentRejected, "HTTP 422", "ERROR"),
             (429, AiAgentUnavailable, "HTTP 429", "WARNING"),
             (500, AiAgentUnavailable, "HTTP 500", "WARNING"),
             (503, AiAgentUnavailable, "HTTP 503", "WARNING"),
             (307, AiAgentUnavailable, "HTTP 307", "WARNING")]
    for status, error, cause, level in cases:
        agent = FakeAiAgent((status, brief()))
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=LOGGER), pytest.raises(error) as info:
            await _call(agent)
        assert (str(info.value), len(agent.requests), _levels(caplog)) == (cause, 1, [level]), status
        assert AGENT_URL not in str(info.value)
    assert caplog.records[-1].getMessage() == "ai-agent /brief/macro answered HTTP 307"   # redirect not followed
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(AiAgentUnavailable):
        await _call(FakeAiAgent((404, {"detail": "Not Found"})))
    assert caplog.records[0].getMessage() == "ai-agent has no /brief/macro (HTTP 404)"


@pytest.mark.asyncio
async def test_ai_agent_client_transport_and_timeout(monkeypatch, caplog):
    monkeypatch.setattr(ai_agent_client, "BRIEF_TIMEOUT", 0.05)
    for agent, cause in ((FakeAiAgent((200, httpx.ConnectError("refused"))), "ConnectError"),
                         (FakeAiAgent((200, httpx.ReadTimeout("slow"))), "timeout"),
                         (FakeAiAgent(delay=1.0), "timeout")):                  # the hard bound
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=LOGGER), pytest.raises(AiAgentUnavailable) as info:
            await _call(agent)
        assert (str(info.value), len(agent.requests), _levels(caplog)) == (cause, 1, ["WARNING"])
