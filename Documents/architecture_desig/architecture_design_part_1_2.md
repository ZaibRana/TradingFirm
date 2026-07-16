# Part 1: High-Level Architecture

### Design Principles (Before We Draw Anything)

| Principle | What it means | Why |
|-----------|--------------|-----|
| **Service isolation** | Each service is a separate process with its own API | If Signal Engine crashes, Data Engine keeps scanning. Users still see their watchlist |
| **Shared nothing** | Services communicate via APIs and a message bus, never by importing each other's code | No tangled dependencies. You can rewrite one service without touching others |
| **Data adapter pattern** | All external APIs (yfinance today, Polygon tomorrow) are behind a swap-layer | Migrating data sources = editing one file, not the whole system |
| **Local-first, cloud-ready** | Runs on Docker Compose on your MacBook. Same containers deploy to cloud unchanged | Zero rewrite when you scale |
| **Progressive complexity** | Start with 3 services (Phase 1). Add AI and Risk Shield later | You ship fast, then grow |

---

### The Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        YOUR MACBOOK (now)                          │
│                     CLOUD (GCP/AWS) (later)                        │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    Docker Compose                            │   │
│  │                                                              │   │
│  │  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐   │   │
│  │  │  SERVICE 1   │    │  SERVICE 2    │    │  SERVICE 3    │   │   │
│  │  │  Data Engine │    │  Signal       │    │  Risk Shield  │   │   │
│  │  │  (FastAPI)   │    │  Engine       │    │  (FastAPI)    │   │   │
│  │  │              │    │  (FastAPI)    │    │               │   │   │
│  │  │ • Scanner    │    │ • S/R zones   │    │ • VIX monitor │   │   │
│  │  │ • Data fetch │    │ • Entry/Exit  │    │ • Breadth     │   │   │
│  │  │ • Indicators │    │ • Stop Loss   │    │ • Crash guard │   │   │
│  │  │ • Fundamentls│    │ • Confidence  │    │ • Health score│   │   │
│  │  └──────┬───────┘    └──────┬───────┘    └──────┬────────┘   │   │
│  │         │                   │                   │            │   │
│  │         │        ┌──────────┴───────────┐       │            │   │
│  │         └────────┤    REDIS (Message     ├──────┘            │   │
│  │                  │    Bus + Cache)       │                   │   │
│  │                  └──────────┬────────────┘                   │   │
│  │                             │                                │   │
│  │  ┌──────────────────────────┼───────────────────────────┐    │   │
│  │  │                  SUPABASE / POSTGRESQL               │    │   │
│  │  │  (Shared database — each service owns its tables)    │    │   │
│  │  │                                                      │    │   │
│  │  │  data_engine.*  │  signals.*  │  risk.*  │  users.*  │    │   │
│  │  └──────────────────────────────────────────────────────┘    │   │
│  │                             │                                │   │
│  │  ┌──────────────────────────┼──────────────────────────┐     │   │
│  │  │              SERVICE 4: WEB APP                     │     │   │
│  │  │              (Next.js 16)                           │     │   │
│  │  │                                                     │     │   │
│  │  │  • Dashboard        • Auth (Supabase Auth)          │     │   │
│  │  │  • Watchlist view   • Push notifications            │     │   │
│  │  │  • Signal cards     • Trade tracker                 │     │   │
│  │  │  • Risk gauge       • Settings                      │     │   │
│  │  └─────────────────────────────────────────────────────┘     │   │
│  │                             │                                │   │
│  │  ┌──────────────────────────┼──────────────────────────┐     │   │
│  │  │              SERVICE 5: AI AGENT                    │     │   │
│  │  │              (Gemini 2.5 Flash)                     │     │   │
│  │  │                                                     │     │   │
│  │  │  • Signal grading   • Pattern learning              │     │   │
│  │  └─────────────────────────────────────────────────────┘     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│                          ┌─────────────┐                            │
│                          │  USERS      │                            │
│                          │  Browser    │                            │
│                          │  (later:    │                            │
│                          │  iOS/Android│                            │
│                          │   apps)     │                            │
│                          └─────────────┘                            │
└─────────────────────────────────────────────────────────────────────┘
```

---

### How Services Talk to Each Other

```
Data Engine ──(REST API)──→ Signal Engine
    "Here are 40 qualified stocks with indicators"

Signal Engine ──(Redis pub/sub)──→ Web App
    "New signal: BUY MSFT at $420, SL $412, TP $440"

Risk Shield ──(Redis pub/sub)──→ Web App + Signal Engine
    "ALERT: Market health dropped to 30/100 — pause new signals"

AI Agent ──(REST API)──→ Signal Engine
    "Signal confidence: 82/100. Reason: sector momentum strong"

Web App ──(REST API)──→ All services
    "User requested manual scan" / "User logged a trade"
```

### What Happens When a Service Goes Down?

| Service down | Impact | User experience |
|-------------|--------|----------------|
| **Data Engine** | No new scans run. Signal Engine uses last cached watchlist | User sees "Last scan: 2 hours ago" — existing data still works |
| **Signal Engine** | No new alerts generated | User sees watchlist + risk gauge, just no new signal cards |
| **Risk Shield** | No crash guard alerts | Signals still fire, user just loses the safety net temporarily |
| **AI Agent** | No confidence scores | Signals show "Score: N/A" — everything else works |
| **Web App** | Users can't access dashboard | Backend keeps scanning and storing — data is ready when app recovers |
| **Redis** | No real-time push. Services fall back to polling database | Slightly delayed updates, nothing breaks |
| **Database** | ❌ Full stop — this is the critical dependency | This is why we use Supabase managed (99.9% uptime) |

---

### Local → Cloud Migration Path

| Phase | Where it runs | How |
|-------|--------------|-----|
| **Now** | MacBook | `docker compose up` — all 5 services + Redis + Postgres in containers |
| **Testing** | MacBook | Same. Access via `localhost:3000` |
| **Beta (10 users)** | Single VPS ($20/mo DigitalOcean or GCP e2-medium) | Same `docker-compose.yml`, just on a cloud VM |
| **Growth (1000 users)** | Supabase (managed DB) + GCP Cloud Run (containers) | Split: DB on Supabase Pro ($25/mo), services on Cloud Run (pay-per-use) |
| **Scale (1M users)** | Full cloud | Supabase Team/Enterprise, Cloud Run auto-scaling, Redis Cloud, CDN |

---

### Folder Structure (Monorepo)

```
TradingFirm/
├── docker-compose.yml          ← Orchestrates everything
├── docker-compose.dev.yml      ← Dev overrides (hot reload, debug)
│
├── services/
│   ├── data-engine/            ← SERVICE 1
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py             ← FastAPI app
│   │   ├── providers/          ← Data adapter layer (swap APIs here)
│   │   │   ├── __init__.py
│   │   │   ├── yfinance_provider.py    ← Dev
│   │   │   └── fmp_provider.py         ← Prod (swap later)
│   │   ├── scanners/
│   │   ├── indicators/
│   │   └── tests/
│   │
│   ├── signal-engine/          ← SERVICE 2
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── zones/              ← S/R detection
│   │   ├── signals/            ← Entry/Exit logic
│   │   └── tests/
│   │
│   ├── risk-shield/            ← SERVICE 3
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   ├── monitors/           ← VIX, breadth, sector rotation
│   │   └── tests/
│   │
│   └── ai-agent/               ← SERVICE 5
│       ├── Dockerfile
│       ├── main.py
│       ├── grading/
│       └── prompts/
│
├── web/                        ← SERVICE 4 (Next.js 16)
│   ├── Dockerfile
│   ├── package.json
│   ├── app/
│   └── components/
│
├── shared/                     ← Shared schemas, types, constants
│   ├── models.py               ← Pydantic models used across services
│   └── constants.py
│
└── infra/                      ← Deployment configs
    ├── supabase/
    │   └── migrations/         ← Database schema
    └── cloud-run/              ← GCP deployment configs (later)
```

---

# Part 2: Tech Stack Decisions — Verified

Every choice below was researched and audited. For each, I list **what we chose**, **what we rejected**, and **why** — including gotchas.

---

### 🔵 Backend: Python 3.12+ with FastAPI

| | |
|---|---|
| **Chosen** | **FastAPI** (ASGI, async-native) |
| **Rejected** | Flask (sync-only, 3–5x slower throughput), Django (too heavy, ORM we don't need since we use Supabase) |

**Why FastAPI:**
- Async-native — your scanner fetches data from 5+ API endpoints simultaneously. FastAPI handles this without blocking
- Auto-generates OpenAPI docs (`/docs`) — your Next.js frontend can auto-generate a typed API client from it
- Built-in Pydantic validation — every request/response is type-checked. Bad data can't enter the system
- 15,000–20,000 req/s vs Flask's 2,000–4,000 req/s
- Trading firms use async backends. This is the right tool

**⚠️ Gotcha to avoid:**
> You must use **async HTTP clients** (`httpx`, not `requests`) and **async DB drivers** (`asyncpg`, not `psycopg2`) inside FastAPI. Using blocking libraries inside `async def` negates all performance gains. I will enforce this in the code.

---

### 🟢 Frontend: Next.js 16 (App Router)

| | |
|---|---|
| **Chosen** | **Next.js 16.2.x** (current stable, Turbopack default) |
| **Rejected** | Vite+React (no SSR, no SEO), SvelteKit (smaller ecosystem), Remix (less adoption) |

**Why Next.js 16:**
- You already have a Next.js app in [/app](file:///Users/zubair/Desktop/TradingFirm/app) — we evolve it, not rewrite
- App Router is now stable and industry-standard in 2026
- Turbopack is the default bundler — fast dev rebuilds
- Explicit caching (`use cache`) — no more "magic" caching bugs from v13/14 era
- SSR = SEO for your marketing pages. SPA mode for the dashboard
- Largest React ecosystem = most libraries, most hiring talent

**⚠️ Gotcha to avoid:**
> Next.js 15 hits end-of-life October 2026. Your current `package.json` shows `"next": "16.2.10"` — you're already on 16. ✅ No migration needed.

---

### 🟡 Database: PostgreSQL via Supabase

| | |
|---|---|
| **Chosen** | **Supabase** (managed PostgreSQL + Auth + Row-Level Security) |
| **Rejected** | Firebase Firestore (NoSQL = wrong for financial data, unpredictable pricing at scale), raw self-hosted Postgres (too much ops work for a solo dev) |

**Why Supabase over Firebase:**

| Factor | Firebase | Supabase | Winner |
|--------|----------|----------|--------|
| Data model | NoSQL (documents) | SQL (relational tables) | 🏆 **Supabase** — financial data is relational (stocks → signals → trades → users) |
| Pricing at 1M users | Unpredictable (pay per read/write) | Predictable (flat tiers) | 🏆 **Supabase** |
| Security model | Security Rules (JSON-like) | Row Level Security (SQL) | 🏆 **Supabase** — RLS enforced at DB level, unhackable from frontend |
| Auth | Good (50K MAU free) | Good (50K MAU free) | Tie |
| Real-time | Excellent | Good (via Postgres Changes) | Firebase slightly better, but we use Redis for this |
| Vendor lock-in | High (proprietary) | Low (standard Postgres — can migrate to any Postgres host) | 🏆 **Supabase** |

**Free tier limits (verified):**
- 500 MB database, 2 active projects, 50K MAU auth, 1 GB storage
- Pauses after 1 week inactivity (just ping it or set a cron)
- Pro tier: $25/mo — 8 GB storage, 100K MAU. Enough until ~10K users

**⚠️ Gotcha to avoid:**
> Remove the Firebase dependency from your app before going further. We'll migrate auth to Supabase Auth. Keeping both = double complexity for zero benefit.

---

### 🔴 Message Bus: Redis

| | |
|---|---|
| **Chosen** | **Redis 7+** (pub/sub + caching + lightweight job queue) |
| **Rejected** | RabbitMQ (overkill for our scale), Kafka (enterprise-grade, way too heavy), direct HTTP polling (wasteful) |

**Why Redis:**
- Runs in a Docker container, works on MacBook, works in cloud. Zero config
- Pub/sub: Risk Shield publishes "market health changed" → Web App and Signal Engine subscribe instantly
- Caching: Store last scan results so the dashboard loads in <100ms
- Lightweight job queue via **Huey** — no need for Celery's complexity

**⚠️ Gotcha to avoid:**
> Redis is an **in-memory** store. If it crashes, cached data is lost. This is fine — we treat Redis as ephemeral. All persistent data lives in PostgreSQL. Redis is speed, not storage.

---

### 🟣 Task Scheduler: Huey (with Redis backend)

| | |
|---|---|
| **Chosen** | **Huey** (lightweight Python task queue) |
| **Rejected** | Celery (complex config, needs RabbitMQ or Redis + flower + beat — 4 moving parts for what should be simple), BullMQ Python (alpha status — not production ready), cron (no retry logic, no monitoring) |

**What Huey handles:**
- Run scanner every morning at 8:00 AM ET
- Run Risk Shield health check every 5 minutes
- Retry failed API calls with exponential backoff
- Queue enrichment tasks (fetch fundamentals for 40 stocks in parallel)

---

### 🟤 Auth: Supabase Auth

| | |
|---|---|
| **Chosen** | **Supabase Auth** (comes free with Supabase) |
| **Rejected** | Firebase Auth (we're dropping Firebase), Clerk ($20/mo+, overkill for B2C), Auth0 (expensive at scale), DIY JWT (security risk) |

**Why Supabase Auth:**
- Free with your Supabase project — 50K MAU included
- Email/password, Google, GitHub login out of the box
- Integrated with Row Level Security — `auth.uid()` in SQL policies means users can only see their own data
- Works with Next.js via `@supabase/ssr` package

---

### 🔵 AI/LLM: Gemini 2.5 Flash

| | |
|---|---|
| **Chosen** | **Gemini 2.5 Flash** via direct API (Google AI Studio key) |
| **Rejected** | GPT-4o-mini (no Google ecosystem), Claude Haiku (no Firebase tie-in), local Llama (too slow on MacBook for real-time) |

**Why Gemini Flash:**
- Free tier: 1,500 req/day (enough for testing and early users)
- Cheapest at scale: ~$0.15/1M input tokens
- 1M token context window — can fit months of signal history for pattern learning
- Structured JSON output mode — returns clean signal grades, not prose

---

### 🟢 Containerization: Docker + Docker Compose

| | |
|---|---|
| **Chosen** | **Docker Compose** for local dev and early production |
| **Rejected** | Kubernetes (massive overkill until 100K+ users), bare-metal/venv (not reproducible, "works on my machine" problems) |

---

### 📊 Full Stack at a Glance

| Layer | Technology | Version | Status |
|-------|-----------|---------|--------|
| **Frontend** | Next.js (App Router) | 16.2.x | ✅ Stable, current |
| **Backend** | Python + FastAPI | 3.12+ / 0.115+ | ✅ Stable, production-proven |
| **Database** | PostgreSQL via Supabase | 15+ | ✅ Stable, managed |
| **Auth** | Supabase Auth | Bundled | ✅ Stable, 50K MAU free |
| **Cache/PubSub** | Redis | 7+ | ✅ Stable, battle-tested |
| **Task Queue** | Huey | 2.5+ | ✅ Stable, lightweight |
| **AI** | Gemini 2.5 Flash | Latest | ✅ GA, free tier generous |
| **Containers** | Docker + Compose | Latest | ✅ Industry standard |
| **Data (dev)** | yfinance + Finviz | Latest | ⚠️ Dev only, not commercial |
| **Data (prod)** | FMP + Twelve Data | Paid tier | ✅ Commercial license |

### 💰 Total Cost During Development

| Item | Cost |
|------|------|
| Supabase (free tier) | $0 |
| Redis (Docker container) | $0 |
| Gemini Flash (free tier) | $0 |
| yfinance/Finviz (dev) | $0 |
| Docker (free) | $0 |
| **Total** | **$0/mo** |
