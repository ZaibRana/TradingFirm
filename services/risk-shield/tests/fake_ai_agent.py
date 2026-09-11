"""A fake ai-agent for Part 3.6b: brief builders over the contract-valid
fixture, one body per contract rule broken, and an httpx.MockTransport that
answers POST /brief/macro from a script. No socket."""

import asyncio
import json
from pathlib import Path

import httpx

from ai_agent_client import AiAgentClient

FIXTURE = Path(__file__).parent / "fixtures" / "ai_agent" / "brief_macro.json"
AGENT_URL = "http://ai-agent.test:8004"
DROP = object()


def brief(**over) -> dict:
    """The fixture with fields replaced; DROP removes one."""
    body = {**json.loads(FIXTURE.read_text()), **over}
    return {k: v for k, v in body.items() if v is not DROP}


def contract_violations() -> dict:
    """name → a body that breaks one rule (bytes are sent raw)."""
    return {
        "missing regimeView": brief(regimeView=DROP),
        "blank oneParagraph": brief(oneParagraph="  \n "),
        "oneParagraph 2001 chars": brief(oneParagraph="p" * 2001),
        "keyRisks 0 items": brief(keyRisks=[]),
        "keyRisks 9 items": brief(keyRisks=["a risk"] * 9),
        "keyRisks item 301 chars": brief(keyRisks=["r" * 301]),
        "extra key": brief(regime_view="the plan's snake case"),
        "not an object": [brief()],
        "not JSON": b"<html>502 Bad Gateway</html>",
        "NaN": json.dumps(brief(keyRisks=[float("nan")])).encode(),
        "over 16 KB": brief(regimeView="\U0001F4C8" * 1000, oneParagraph="\U0001F4C8" * 2000),
    }


class FakeAiAgent:
    """Answers each call with the next (status, body), the last one repeating.
    body: dict / list (JSON), bytes (raw) or an exception to raise. `delay`
    seconds before each answer. Every request is kept."""

    def __init__(self, *answers, delay: float = 0.0):
        self.answers = list(answers) or [(200, brief())]
        self.delay, self.requests = delay, []

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers[min(len(self.requests), len(self.answers)) - 1]
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(body, Exception):
            raise body
        headers = {"location": f"{AGENT_URL}/elsewhere"} if 300 <= status < 400 else None
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    def client(self, url: str = AGENT_URL) -> AiAgentClient:
        return AiAgentClient(url, transport=httpx.MockTransport(self._handle))
