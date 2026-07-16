# Part 7: Service 5 — AI Agent (Claude / LLM Intelligence Layer)

### First: Claude vs Gemini — Honest Comparison for YOUR Use Case

You asked about Claude. Here are the real numbers:

| Factor | Claude Haiku 4.5 | Gemini 3 Flash | Winner for us |
|--------|:--:|:--:|:--:|
| **Input cost** | $1.00 / MTok | $0.50 / MTok | 💰 Gemini (2x cheaper) |
| **Output cost** | $5.00 / MTok | $3.00 / MTok | 💰 Gemini (1.7x cheaper) |
| **Context window** | 200K tokens | 1M tokens | 🏆 Gemini (5x more history) |
| **Free tier** | ❌ None | ✅ 1,500 req/day free | 🏆 Gemini |
| **Structured JSON output** | ✅ Native (strict mode, guaranteed schema) | ✅ Native (structured output mode) | Tie |
| **Reasoning quality** | ✅ Excellent — nuanced, careful | ✅ Good — fast, practical | 🏆 Claude (better at "why" explanations) |
| **Speed (latency)** | ~1.2s for short responses | ~0.5s for short responses | 🏆 Gemini (faster for real-time) |

| Factor | Claude Sonnet 5 | Gemini 3.5 Flash | Winner for us |
|--------|:--:|:--:|:--:|
| **Input cost** | $2.00 / MTok | $1.50 / MTok | 💰 Gemini (slightly cheaper) |
| **Output cost** | $10.00 / MTok | $9.00 / MTok | Roughly equal |
| **Context window** | 1M tokens | 1M tokens | Tie |
| **Reasoning quality** | ✅ Exceptional | ✅ Very good | 🏆 Claude (noticeably better at nuance) |

> **Verdict: Use Claude Haiku 4.5 for development ($0/mo via free credits), upgrade to Claude Sonnet 5 when you go commercial.** The cost difference is negligible for your volume. Quality wins.

**Fallback strategy:** If Anthropic has an outage or rate limits you, the system automatically falls back to Gemini Flash. Same interface, different provider. Just like the data adapter pattern.

---

### What It Does (Responsibilities)

| Responsibility | Description |
|---------------|-------------|
| **Signal grading** | Score each signal 0–100 based on context, history, and market conditions |
| **Pattern learning** | Analyze past signal outcomes to find what works and what doesn't |
| **Trade reasoning** | Explain WHY a signal is strong/weak in plain English |
| **Weekly reports** | Generate natural language summary of performance + market outlook |
| **Natural language queries** | User asks "What tech stocks look good this week?" → structured answer |
| **Adaptive improvement** | Track its own accuracy. If it's wrong > 40% of the time → flag for review |

---

### Internal Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI AGENT (FastAPI)                           │
│                    Port: 8004                                   │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  API Layer (main.py)                                      │  │
│  │                                                           │  │
│  │  GET  /health             → service status                │  │
│  │  POST /grade/signal       → grade a single signal         │  │
│  │  POST /grade/batch        → grade multiple signals        │  │
│  │  POST /analyze/patterns   → find patterns in history      │  │
│  │  POST /query              → natural language question      │  │
│  │  GET  /reports/weekly     → latest weekly report          │  │
│  │  POST /reports/generate   → trigger report generation     │  │
│  │  GET  /accuracy           → self-assessment metrics       │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  LLM Provider Layer (providers/)                          │  │
│  │                                                           │  │
│  │  ┌────────────────────┐  ┌────────────────────────────┐  │  │
│  │  │ claude_provider.py │  │ gemini_provider.py         │  │  │
│  │  │ (PRIMARY)          │  │ (FALLBACK)                 │  │  │
│  │  │                    │  │                            │  │  │
│  │  │ grade_signal()     │  │ grade_signal()             │  │  │
│  │  │ analyze_patterns() │  │ analyze_patterns()         │  │  │
│  │  │ answer_query()     │  │ answer_query()             │  │  │
│  │  │ generate_report()  │  │ generate_report()          │  │  │
│  │  └────────────────────┘  └────────────────────────────┘  │  │
│  │                                                           │  │
│  │  Selected by: LLM_PROVIDER=claude (env variable)          │  │
│  │  Fallback: if primary fails 3x → auto-switch to fallback  │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Grading Logic (grading/)                                 │  │
│  │                                                           │  │
│  │  signal_grader.py     → Build context, call LLM, parse   │  │
│  │  pattern_analyzer.py  → Feed history, extract patterns    │  │
│  │  report_generator.py  → Weekly performance + outlook      │  │
│  │  accuracy_tracker.py  → Compare AI grades vs outcomes     │  │
│  └──────────────────┬────────────────────────────────────────┘  │
│                     │                                           │
│  ┌──────────────────┼────────────────────────────────────────┐  │
│  │  Prompt Templates (prompts/)                              │  │
│  │                                                           │  │
│  │  signal_grade.txt      → System + user prompt for grading │  │
│  │  pattern_analysis.txt  → Prompt for pattern detection     │  │
│  │  weekly_report.txt     → Report generation template       │  │
│  │  query_answer.txt      → NL query answering template      │  │
│  │                                                           │  │
│  │  Prompts are VERSION CONTROLLED. Every change is tracked. │  │
│  │  This is critical for reproducibility.                    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

### File Structure

```
services/ai-agent/
├── Dockerfile
├── requirements.txt
├── main.py                      ← FastAPI app + routes
├── config.py                    ← LLM provider selection, rate limits, model names
│
├── providers/                   ← LLM ADAPTER LAYER (same pattern as data providers)
│   ├── __init__.py              ← Factory: returns provider based on env
│   ├── base.py                  ← Abstract LLM interface
│   ├── claude_provider.py       ← Primary: Anthropic Claude API
│   └── gemini_provider.py       ← Fallback: Google Gemini API
│
├── grading/                     ← CORE LOGIC
│   ├── __init__.py
│   ├── signal_grader.py         ← Build context → call LLM → parse grade
│   ├── pattern_analyzer.py      ← Historical pattern detection
│   ├── report_generator.py      ← Weekly report creation
│   ├── query_handler.py         ← Natural language question answering
│   └── accuracy_tracker.py      ← Track AI accuracy over time
│
├── prompts/                     ← PROMPT TEMPLATES (version controlled)
│   ├── signal_grade.md          ← Signal grading system prompt
│   ├── pattern_analysis.md      ← Pattern detection prompt
│   ├── weekly_report.md         ← Report generation prompt
│   └── query_answer.md          ← NL query prompt
│
├── models/
│   ├── __init__.py
│   ├── grade.py                 ← SignalGrade, GradeBreakdown
│   └── report.py                ← WeeklyReport, PatternInsight
│
└── tests/
    ├── test_grading.py
    ├── test_prompts.py          ← Test prompt outputs are valid JSON
    └── test_accuracy.py
```

---

### The LLM Provider Interface (Claude ↔ Gemini Swap)

```python
# providers/base.py — Same adapter pattern as Data Engine

from abc import ABC, abstractmethod

class LLMProvider(ABC):
    """Every LLM provider implements this interface."""

    @abstractmethod
    async def grade_signal(self, signal_context: dict) -> dict:
        """Return { score: int, reasoning: str, factors: list }"""
        ...

    @abstractmethod
    async def analyze_patterns(self, history: list[dict]) -> dict:
        """Return { patterns: list, recommendations: list }"""
        ...

    @abstractmethod
    async def answer_query(self, question: str, context: dict) -> str:
        """Return natural language answer."""
        ...

    @abstractmethod
    async def generate_report(self, weekly_data: dict) -> dict:
        """Return { summary: str, highlights: list, outlook: str }"""
        ...
```

```python
# providers/__init__.py — Factory with automatic fallback

import os
from .base import LLMProvider

def get_provider() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "claude")

    if provider == "claude":
        from .claude_provider import ClaudeProvider
        return ClaudeProvider()
    elif provider == "gemini":
        from .gemini_provider import GeminiProvider
        return GeminiProvider()
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
```

---

### How Signal Grading Works

```
Signal Engine calls: POST /grade/signal
│
├── 1. Build context package:
│   ├── Signal data (ticker, entry, SL, TP, zone info)
│   ├── Technical indicators (from Data Engine)
│   ├── Fundamental score (from Data Engine)
│   ├── Market health (from Risk Shield)
│   ├── Sector performance (last 5 days)
│   └── Past signals for same ticker (last 30 days + outcomes)
│
├── 2. Load prompt template (prompts/signal_grade.md)
│   ├── System prompt: "You are a senior trading analyst..."
│   └── User prompt: structured JSON context
│
├── 3. Call Claude API with strict JSON output schema:
│   ├── Model: claude-haiku-4.5 (dev) / claude-sonnet-5 (prod)
│   ├── Output schema enforced (no parsing errors possible)
│   └── Max tokens: 500 (keeps cost low, forces concision)
│
├── 4. Parse structured response:
│   {
│     "score": 78,
│     "conviction": "HIGH",
│     "reasoning": "Strong support zone tested 3x with increasing 
│                   volume. Sector (Tech) outperforming. MACD 
│                   crossing bullish on 1H. Risk: VIX at 22 adds 
│                   uncertainty. Fundamental score (82) supports 
│                   quality.",
│     "factors": {
│       "zone_quality": "+15 — tested 3x, volume confirmed",
│       "trend_alignment": "+12 — all timeframes bullish",
│       "fundamental_support": "+8 — strong P/E, growing EPS",
│       "risk_environment": "-5 — VIX slightly elevated",
│       "historical_pattern": "+3 — similar setups hit TP1 68% of time"
│     },
│     "similar_past_signals": 12,
│     "similar_win_rate": 0.68
│   }
│
└── 5. Return grade to Signal Engine → added to signal confidence
```

---

### The Learning Loop (How It Gets Smarter)

```
EVERY SUNDAY AT 6:00 PM ET:
│
├── 1. Fetch all signal outcomes from past 30 days
│      (from Signal Engine /signals/history?days=30)
│
├── 2. For each signal that had an AI grade:
│      Compare: AI predicted score vs actual outcome
│      ├── AI said 80+ and signal HIT_TP1? → AI was right ✅
│      ├── AI said 80+ and signal STOPPED_OUT? → AI was wrong ❌
│      ├── AI said <50 and signal STOPPED_OUT? → AI was right ✅
│      └── AI said <50 and signal HIT_TP2? → AI missed it ❌
│
├── 3. Calculate accuracy metrics:
│      ├── Overall accuracy (% of correct directional calls)
│      ├── High-conviction accuracy (grades 75+, did they hit?)
│      ├── Sector accuracy (is AI better at Tech than Energy?)
│      └── Zone-type accuracy (better at support bounces vs breakouts?)
│
├── 4. Feed accuracy report INTO the next week's grading prompt:
│      "Last week you graded 45 signals. Your accuracy:
│       - Overall: 64% correct
│       - You overrated Energy sector signals (40% hit rate)
│       - You underrated Healthcare bounces (85% hit rate)
│       Adjust your grading accordingly."
│
├── 5. Store learning log in PostgreSQL
│      (timestamped, queryable, auditable)
│
└── 6. Generate weekly report (pushed to Web App)
```

---

### What the Weekly Report Looks Like

```json
{
  "week": "2026-07-07 to 2026-07-13",
  "generated_at": "2026-07-13T18:00:00Z",

  "performance": {
    "signals_generated": 42,
    "signals_triggered": 28,
    "wins": 18,
    "losses": 7,
    "expired": 3,
    "win_rate": 0.72,
    "avg_rr_achieved": 1.8,
    "best_signal": "MSFT +$8.20 (TP2 hit, R:R 3.2:1)",
    "worst_signal": "ENPH -$4.10 (SL hit after earnings miss)"
  },

  "ai_accuracy": {
    "overall": 0.67,
    "high_conviction": 0.78,
    "by_sector": {
      "Technology": 0.75,
      "Healthcare": 0.80,
      "Energy": 0.45
    },
    "self_assessment": "I consistently overrate Energy sector signals 
                        near resistance zones. Reducing Energy confidence 
                        by 10% next week."
  },

  "market_outlook": "Market health averaged 68 (CAUTIOUS). SPY held 
                     above 50 EMA but breadth narrowed. Sector rotation 
                     slightly defensive. Recommend smaller position sizes 
                     and focusing on high-conviction (80+) signals only.",

  "top_patterns_found": [
    "Support bounces with RVOL > 2.0 hit TP1 82% of the time",
    "Signals during CAUTIOUS regime had 15% lower win rate",
    "Healthcare stocks had highest consistency this month"
  ]
}
```

---

### API Endpoints Detail

| Endpoint | Method | What it does | Who calls it |
|----------|--------|-------------|-------------|
| `/health` | GET | Service status + last grading time | Docker, monitoring |
| `/grade/signal` | POST | Grade a single signal. Body: signal context JSON | Signal Engine (per signal) |
| `/grade/batch` | POST | Grade multiple signals. Body: array of contexts | Signal Engine (after scan) |
| `/analyze/patterns` | POST | Find patterns in signal history | Huey cron (weekly) |
| `/query` | POST | Answer natural language question. Body: `{ "question": "..." }` | Web App (AI chat feature) |
| `/reports/weekly` | GET | Get latest weekly report | Web App (reports page) |
| `/reports/generate` | POST | Force generate new report | Huey cron (Sunday 6 PM) |
| `/accuracy` | GET | AI self-assessment metrics | Web App (AI transparency dashboard) |

---

### Cost Projections

| Usage level | Calls/day | Tokens/day | Monthly cost (Haiku 4.5) | Monthly cost (Sonnet 5) |
|------------|:---------:|:----------:|:----:|:----:|
| **Dev/testing** | 20 | ~40K | ~$0.26 | ~$1.00 |
| **Live (1 user)** | 200 | ~400K | ~$2.60 | ~$10.00 |
| **Growth (100 users)** | 500 | ~1M | ~$6.50 | ~$25.00 |
| **Scale (1000 users)** | 1,000 | ~2M | ~$13.00 | ~$50.00 |

---

### Automatic Fallback Logic

```
CALL Claude API
│
├── Success? → Return result
│
├── Fail (timeout / 5xx / rate limit)?
│   ├── Retry 1 (after 2s)
│   ├── Retry 2 (after 5s)
│   ├── Retry 3 (after 10s)
│   │
│   └── All 3 failed?
│       ├── Log: "Claude unreachable, switching to Gemini fallback"
│       ├── Call Gemini Flash with SAME prompt
│       ├── Tag response: { "provider": "gemini", "fallback": true }
│       └── After 15 min → retry Claude again
│
└── Rate limited (429)?
    ├── Use Gemini for remaining calls this minute
    └── Resume Claude next minute
```

| Scenario | What happens | User impact |
|----------|-------------|-------------|
| Claude works normally | All grades from Claude | Best quality |
| Claude has 30-second outage | 3 retries → Gemini fallback | Slight quality dip, user never notices |
| Claude down for hours | Full Gemini mode | Lower quality grades, flagged in UI as "AI: Gemini (fallback)" |
| Both down | Signals generated WITHOUT AI grade | Confidence shows "Score: N/A (AI unavailable)" |

---

### Dependencies (`requirements.txt`)

| Package | Purpose | Status |
|---------|---------|--------|
| `fastapi` | Web framework | ✅ Stable |
| `uvicorn[standard]` | ASGI server | ✅ Stable |
| `anthropic` | Claude API client (official SDK) | ✅ Stable, structured output support |
| `google-generativeai` | Gemini API client (fallback) | ✅ Stable |
| `httpx` | Async calls to Data Engine, Signal Engine, Risk Shield | ✅ Stable |
| `redis` | Cache grades, subscribe to events | ✅ Stable |
| `asyncpg` | Store grades + accuracy history | ✅ Stable |
| `huey[redis]` | Weekly report cron + pattern analysis | ✅ Stable |
| `jinja2` | Prompt template rendering | ✅ Stable |

---

### Security Considerations

| Threat | Mitigation |
|--------|-----------|
| **Prompt injection** (malicious ticker name tricks LLM) | All inputs sanitized before prompt. Ticker validated against whitelist. No user text goes raw into prompts |
| **LLM hallucination** (makes up price targets) | LLM only scores/explains — it NEVER generates entry/SL/TP numbers. Those come from the math in Signal Engine |
| **API key exposure** | Keys in `.env`, loaded via `config.py`. Never logged, never in responses |
| **Cost runaway** | Hard limit: max 2,000 API calls/day. Alert if > 1,500. Kill switch in config |
| **Accuracy decay** | Weekly accuracy check. If overall accuracy drops below 55% for 2 consecutive weeks → auto-disable AI grades, alert admin |
| **Bias amplification** | Learning loop shows raw numbers, not curated wins. AI sees all losses equally |

---

### What Makes This Different From "Just Calling ChatGPT"

| Approach | What most people do | What we do |
|----------|-------------------|------------|
| **Context** | "Is MSFT a good buy?" (zero context) | Send 2K tokens of structured data: zone strength, RVOL, sector performance, past signals, market health |
| **Output** | Free-form text, hope for the best | Strict JSON schema — guaranteed parseable, no surprises |
| **Accountability** | No tracking of accuracy | Weekly accuracy audit. AI sees its own track record |
| **Learning** | Same prompt forever | Prompt evolves weekly with real performance data |
| **Fallback** | App crashes if API is down | Auto-switch to Gemini. App works without AI entirely |
| **Role** | AI makes the decision | AI **advises**. Math makes the decision. Human executes |

---

### Updated Tech Stack (Revised from Part 2)

| Layer | Technology | Change from Part 2 |
|-------|-----------|-------------------|
| **AI (Primary)** | Claude Haiku 4.5 (dev) → Claude Sonnet 5 (prod) | Changed from Gemini Flash per your preference |
| **AI (Fallback)** | Gemini 3 Flash | Kept as automatic fallback |
| Everything else | Unchanged | No impact on any other service |
