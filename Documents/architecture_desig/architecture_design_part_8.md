# Part 8: Database Schema, Security, Deployment & Phase Timeline

This is the final part — the foundation everything sits on and the roadmap to build it all.

---

### DATABASE SCHEMA (PostgreSQL via Supabase)

Each service owns its own tables. No service writes to another service's tables directly — they communicate via APIs. But they can READ across schemas for joins (e.g., dashboard needs signals + stocks + health together).

#### Schema Overview

```
┌─────────────────────────────────────────────────────────┐
│                    POSTGRESQL                           │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ data_engine   │  │ signals      │  │ risk         │ │
│  │              │  │              │  │              │ │
│  │ stocks       │  │ signals      │  │ health_checks│ │
│  │ scan_results │  │ zones        │  │ alerts       │ │
│  │ fundamentals │  │ signal_logs  │  │              │ │
│  │ indicators   │  │              │  │              │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐                    │
│  │ users        │  │ ai           │                    │
│  │              │  │              │                    │
│  │ profiles     │  │ grades       │                    │
│  │ trades       │  │ accuracy_logs│                    │
│  │ preferences  │  │ reports      │                    │
│  │ watchlists   │  │ patterns     │                    │
│  └──────────────┘  └──────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

---

#### Table Definitions

##### `data_engine.stocks` — Master stock record

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | `VARCHAR(10) PRIMARY KEY` | Stock symbol (e.g., MSFT) |
| `name` | `VARCHAR(255)` | Company name |
| `sector` | `VARCHAR(100)` | GICS sector |
| `industry` | `VARCHAR(100)` | GICS industry |
| `market_cap` | `BIGINT` | Market cap in dollars |
| `float_shares` | `BIGINT` | Float shares |
| `updated_at` | `TIMESTAMPTZ` | Last enrichment time |

##### `data_engine.scan_results` — Each scan run

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID PRIMARY KEY DEFAULT gen_random_uuid()` | Scan ID |
| `scanned_at` | `TIMESTAMPTZ DEFAULT now()` | When scan ran |
| `market_status` | `VARCHAR(20)` | pre_market / market_open / after_hours |
| `total_screened` | `INTEGER` | Total tickers screened |
| `total_passed` | `INTEGER` | Tickers that passed all filters |
| `duration_seconds` | `FLOAT` | How long the scan took |
| `stocks` | `JSONB` | Full results array (denormalized for fast reads) |

##### `data_engine.fundamentals` — Fundamental data per stock

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | `VARCHAR(10) REFERENCES data_engine.stocks(ticker)` | Stock |
| `pe_ratio` | `FLOAT` | Price/Earnings ratio |
| `eps_growth` | `FLOAT` | EPS growth % YoY |
| `revenue_growth` | `FLOAT` | Revenue growth % YoY |
| `debt_to_equity` | `FLOAT` | Debt/Equity ratio |
| `roe` | `FLOAT` | Return on Equity % |
| `fundamental_score` | `INTEGER` | Composite score 0–100 |
| `updated_at` | `TIMESTAMPTZ` | Last calculation time |

##### `signals.signals` — Every signal generated

| Column | Type | Description |
|--------|------|-------------|
| `id` | `VARCHAR(50) PRIMARY KEY` | e.g., sig_20260715_MSFT_001 |
| `ticker` | `VARCHAR(10)` | Stock symbol |
| `direction` | `VARCHAR(5)` | LONG / SHORT |
| `status` | `VARCHAR(20)` | PENDING / TRIGGERED / HIT_TP1 / HIT_TP2 / HIT_TP3 / STOPPED_OUT / EXPIRED / ADJUSTED |
| `entry_low` | `FLOAT` | Entry zone low |
| `entry_high` | `FLOAT` | Entry zone high |
| `stop_loss` | `FLOAT` | Stop loss price |
| `tp1` | `FLOAT` | Take profit target 1 |
| `tp2` | `FLOAT` | Take profit target 2 |
| `tp3` | `FLOAT` | Take profit target 3 |
| `rr_ratio` | `FLOAT` | Risk:Reward ratio |
| `confidence` | `INTEGER` | 0–100 composite score |
| `confidence_breakdown` | `JSONB` | { zone, confirmations, fundamental, ai, risk_penalty } |
| `zone_data` | `JSONB` | Zone info (type, level, strength, methods) |
| `context` | `JSONB` | Sector, ATRP, RVOL, market_health at signal time |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | When generated |
| `triggered_at` | `TIMESTAMPTZ` | When price entered entry zone |
| `closed_at` | `TIMESTAMPTZ` | When outcome was determined |
| `expires_at` | `TIMESTAMPTZ` | Auto-expiry time (created_at + 48h) |

##### `signals.zones` — Cached S/R zones per stock

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID PRIMARY KEY` | Zone ID |
| `ticker` | `VARCHAR(10)` | Stock symbol |
| `zone_type` | `VARCHAR(20)` | SUPPORT / RESISTANCE / DEMAND / SUPPLY |
| `price_low` | `FLOAT` | Zone lower bound |
| `price_high` | `FLOAT` | Zone upper bound |
| `strength_score` | `INTEGER` | 0–100 zone quality score |
| `methods` | `TEXT[]` | Array: ['fractal', 'volume_cluster', 'pivot'] |
| `times_tested` | `INTEGER` | How many times price bounced here |
| `calculated_at` | `TIMESTAMPTZ` | When zones were last computed |

##### `risk.health_checks` — Health score history

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID PRIMARY KEY` | Check ID |
| `checked_at` | `TIMESTAMPTZ DEFAULT now()` | Timestamp |
| `score` | `INTEGER` | 0–100 health score |
| `regime` | `VARCHAR(20)` | HEALTHY / CAUTIOUS / DANGER / CRITICAL |
| `indicators` | `JSONB` | All 6 indicator readings |
| `trend` | `VARCHAR(20)` | improving / declining / stable |

##### `users.profiles` — User accounts (extends Supabase auth.users)

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID PRIMARY KEY REFERENCES auth.users(id)` | Supabase user ID |
| `display_name` | `VARCHAR(100)` | User's display name |
| `tier` | `VARCHAR(20) DEFAULT 'free'` | free / pro / premium |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Account creation |
| `last_login` | `TIMESTAMPTZ` | Last login time |

##### `users.trades` — User-logged trades

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID PRIMARY KEY` | Trade ID |
| `user_id` | `UUID REFERENCES users.profiles(id)` | Owner |
| `signal_id` | `VARCHAR(50) REFERENCES signals.signals(id)` | Linked signal (nullable) |
| `ticker` | `VARCHAR(10)` | Stock traded |
| `direction` | `VARCHAR(5)` | LONG / SHORT |
| `entry_price` | `FLOAT` | Actual entry price |
| `exit_price` | `FLOAT` | Actual exit price |
| `shares` | `INTEGER` | Quantity |
| `pnl` | `FLOAT` | Profit/Loss in dollars |
| `pnl_percent` | `FLOAT` | P&L as percentage |
| `notes` | `TEXT` | User notes |
| `traded_at` | `TIMESTAMPTZ` | When trade was executed |
| `logged_at` | `TIMESTAMPTZ DEFAULT now()` | When user logged it |

##### `ai.grades` — AI signal grades

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID PRIMARY KEY` | Grade ID |
| `signal_id` | `VARCHAR(50) REFERENCES signals.signals(id)` | Which signal |
| `provider` | `VARCHAR(20)` | claude / gemini |
| `model` | `VARCHAR(50)` | claude-haiku-4.5 / claude-sonnet-5 / gemini-3-flash |
| `score` | `INTEGER` | AI's 0–100 grade |
| `conviction` | `VARCHAR(10)` | LOW / MEDIUM / HIGH |
| `reasoning` | `TEXT` | AI's explanation |
| `factors` | `JSONB` | Breakdown of scoring factors |
| `was_correct` | `BOOLEAN` | Set after signal outcome (for accuracy tracking) |
| `graded_at` | `TIMESTAMPTZ DEFAULT now()` | When graded |

##### `ai.accuracy_logs` — Weekly AI self-assessment

| Column | Type | Description |
|--------|------|-------------|
| `id` | `UUID PRIMARY KEY` | Log ID |
| `week_start` | `DATE` | Monday of the assessed week |
| `total_graded` | `INTEGER` | Signals graded that week |
| `correct_count` | `INTEGER` | How many were correct |
| `accuracy` | `FLOAT` | Correct / total |
| `by_sector` | `JSONB` | { "Technology": 0.75, "Energy": 0.45 } |
| `by_zone_type` | `JSONB` | { "support": 0.70, "resistance": 0.55 } |
| `self_assessment` | `TEXT` | AI's written reflection |
| `logged_at` | `TIMESTAMPTZ DEFAULT now()` | When logged |

---

#### Row Level Security (RLS) Rules

| Table | Rule | Effect |
|-------|------|--------|
| `users.profiles` | `auth.uid() = id` | Users can only read/write their own profile |
| `users.trades` | `auth.uid() = user_id` | Users can only see their own trades |
| `users.preferences` | `auth.uid() = user_id` | Users can only edit their own preferences |
| `signals.signals` | `SELECT` open to all authenticated users | All users can read signals (tier-based filtering in API layer) |
| `data_engine.*` | `SELECT` open to all authenticated users | Market data is not user-specific |
| `risk.*` | `SELECT` open to all authenticated users | Market health is not user-specific |
| `ai.grades` | `SELECT` open to all authenticated users | Grades are signal-level, not user-level |

---

### SECURITY ARCHITECTURE

#### Defense in Depth (4 Layers)

```
┌─────────────────────────────────────────────────────┐
│ LAYER 1: NETWORK                                    │
│ • HTTPS everywhere (TLS 1.3)                        │
│ • CORS whitelist (only your domain)                 │
│ • Rate limiting per IP (60 req/min)                 │
│ • Docker internal network (services can't be        │
│   reached from outside except via Next.js)          │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────┼───────────────────────────────┐
│ LAYER 2: APPLICATION                                │
│ • Supabase Auth (PKCE flow, httpOnly cookies)       │
│ • Next.js API routes validate session before proxy  │
│ • Pydantic input validation on all FastAPI endpoints │
│ • No raw user input in SQL or LLM prompts           │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────┼───────────────────────────────┐
│ LAYER 3: DATABASE                                   │
│ • Row Level Security on ALL user tables             │
│ • Prepared statements (asyncpg — no SQL injection)  │
│ • Connection pooling via Supabase (max 200 pooled)  │
│ • No direct DB access from frontend                 │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────┼───────────────────────────────┐
│ LAYER 4: SECRETS                                    │
│ • All API keys in .env (never committed to git)     │
│ • .gitignore enforced for .env, *.key, secrets/     │
│ • Cloud: secrets in GCP Secret Manager / AWS SSM    │
│ • Rotate keys quarterly                             │
└─────────────────────────────────────────────────────┘
```

#### Specific Threat Mitigations

| Threat | Attack vector | Mitigation |
|--------|-------------|-----------|
| **SQL Injection** | Malformed ticker symbol in URL | Pydantic regex validation (`^[A-Z]{1,5}$`) + asyncpg parameterized queries |
| **XSS** | Injected script in trade notes | React auto-escapes all JSX. CSP headers block inline scripts |
| **CSRF** | Forged request from malicious site | Supabase PKCE + httpOnly cookies. SameSite=Strict |
| **Broken Auth** | Stolen JWT, session hijack | Short-lived sessions (1h). Refresh tokens in httpOnly cookies. No localStorage tokens |
| **Data Breach** | DB compromise | RLS ensures attacker can't read other users' trades even with DB access. Passwords hashed by Supabase (bcrypt) |
| **DDoS** | Flood of scan requests | Rate limiting: 1 scan per 5 min per user. Max 60 API calls/min per IP |
| **Prompt Injection** | Ticker "MSFT; DROP TABLE" | Input sanitized before LLM prompt. Ticker validated against known universe |
| **Supply Chain** | Compromised npm/pip package | Lock files (`package-lock.json`, `requirements.txt` with pinned versions). Dependabot alerts |
| **Insider Threat** | Admin abuse | Audit log on all admin actions. No single person has all secrets |

---

### DEPLOYMENT STRATEGY

#### Phase 1: MacBook (Now)

```yaml
# docker-compose.yml (simplified)
version: "3.9"

services:
  # --- INFRASTRUCTURE ---
  postgres:
    image: postgres:16-alpine
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    environment:
      POSTGRES_DB: tradingfirm
      POSTGRES_USER: tf_user
      POSTGRES_PASSWORD: ${DB_PASSWORD}

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  # --- BACKEND SERVICES ---
  data-engine:
    build: ./services/data-engine
    ports: ["8001:8001"]
    depends_on: [postgres, redis]
    environment:
      DATABASE_URL: postgresql://tf_user:${DB_PASSWORD}@postgres:5432/tradingfirm
      REDIS_URL: redis://redis:6379
      DATA_PROVIDER: yfinance

  signal-engine:
    build: ./services/signal-engine
    ports: ["8002:8002"]
    depends_on: [postgres, redis, data-engine]
    environment:
      DATABASE_URL: postgresql://tf_user:${DB_PASSWORD}@postgres:5432/tradingfirm
      REDIS_URL: redis://redis:6379
      DATA_ENGINE_URL: http://data-engine:8001

  risk-shield:
    build: ./services/risk-shield
    ports: ["8003:8003"]
    depends_on: [postgres, redis]
    environment:
      DATABASE_URL: postgresql://tf_user:${DB_PASSWORD}@postgres:5432/tradingfirm
      REDIS_URL: redis://redis:6379
      DATA_PROVIDER: yfinance

  ai-agent:
    build: ./services/ai-agent
    ports: ["8004:8004"]
    depends_on: [postgres, redis]
    environment:
      DATABASE_URL: postgresql://tf_user:${DB_PASSWORD}@postgres:5432/tradingfirm
      REDIS_URL: redis://redis:6379
      LLM_PROVIDER: claude
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}

  # --- FRONTEND ---
  web:
    build: ./web
    ports: ["3000:3000"]
    depends_on: [data-engine, signal-engine, risk-shield, ai-agent]
    environment:
      NEXT_PUBLIC_SUPABASE_URL: ${SUPABASE_URL}
      NEXT_PUBLIC_SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY}
      DATA_ENGINE_URL: http://data-engine:8001
      SIGNAL_ENGINE_URL: http://signal-engine:8002
      RISK_SHIELD_URL: http://risk-shield:8003
      AI_AGENT_URL: http://ai-agent:8004

volumes:
  pgdata:
```

#### Phase 2: Cloud Migration Path

| Component | MacBook (now) | Cloud (later) | Migration effort |
|-----------|--------------|---------------|:----------------:|
| PostgreSQL | Docker container | Supabase managed | Change `DATABASE_URL` env var | 
| Redis | Docker container | Redis Cloud / Memorystore | Change `REDIS_URL` env var |
| Data Engine | Docker container | GCP Cloud Run | Push image, set env vars |
| Signal Engine | Docker container | GCP Cloud Run | Push image, set env vars |
| Risk Shield | Docker container | GCP Cloud Run | Push image, set env vars |
| AI Agent | Docker container | GCP Cloud Run | Push image, set env vars |
| Web App | Docker container | Vercel or Cloud Run | `vercel deploy` or push image |
| Secrets | `.env` file | GCP Secret Manager | One-time setup |

---

### BUILD PHASE TIMELINE

#### Phase 1: Foundation (Week 1–2)

| Task | What | Deliverable |
|------|------|-------------|
| 1.1 | Project scaffolding — monorepo, Docker Compose, all Dockerfiles | `docker compose up` boots all containers (empty services) |
| 1.2 | Supabase project setup — create DB, configure auth, write migrations | All tables created, RLS rules applied |
| 1.3 | Data Engine — provider interface + yfinance provider | `/scan/run` triggers a scan, `/scan/results` returns data |
| 1.4 | Migrate existing [pro_scan.py](file:///Users/zubair/Desktop/TradingFirm/scanner/pro_scan.py) logic into Data Engine | All existing filters work via API instead of CLI |
| 1.5 | Redis caching for scan results | `/scan/results` returns in <50ms from cache |

---

#### Phase 2: Signals (Week 3–4)

| Task | What | Deliverable |
|------|------|-------------|
| 2.1 | Signal Engine — zone detection (fractal + volume clusters + pivots) | `/zones/MSFT` returns ranked S/R zones |
| 2.2 | Signal Engine — signal generator (detector + calculator + R:R) | `/signals/generate` creates real signals from watchlist |
| 2.3 | Signal Engine — lifecycle tracker | Signals transition PENDING → TRIGGERED → outcome |
| 2.4 | Redis pub/sub — Data Engine publishes scan_complete → Signal Engine auto-runs | End-to-end: scan → zones → signals, fully automated |

---

#### Phase 3: Risk Shield (Week 5)

| Task | What | Deliverable |
|------|------|-------------|
| 3.1 | Risk Shield — all 6 monitors + scoring | `/market/health` returns live 0–100 score |
| 3.2 | Risk Shield → Signal Engine integration | Signal Engine pauses/adjusts when health drops |
| 3.3 | Huey scheduler — 5-min health checks during market hours | Automatic monitoring, alerts on regime change |

---

#### Phase 4: Web App (Week 6–8)

| Task | What | Deliverable |
|------|------|-------------|
| 4.1 | Auth setup — Supabase Auth + login/signup pages | Users can register and log in |
| 4.2 | Dashboard — WatchlistGrid + SectorTabs | Users see scanned stocks with indicators |
| 4.3 | Signal cards — SignalCard + SignalDetail | Users see active signals with entry/SL/TP |
| 4.4 | Risk gauge — RiskGauge component | Users see market health meter |
| 4.5 | Stock detail page — chart with zone overlay | Click a stock → see candlestick chart with S/R zones |
| 4.6 | Trade tracker — TradeLog + PnLChart | Users log trades, see equity curve |
| 4.7 | SSE real-time updates | New signals + health changes push to dashboard live |
| 4.8 | Mobile responsive | Dashboard works on phone browsers |

---

#### Phase 5: AI Agent (Week 9–10)

| Task | What | Deliverable |
|------|------|-------------|
| 5.1 | AI Agent — Claude provider + signal grading | `/grade/signal` returns AI score + reasoning |
| 5.2 | AI Agent → Signal Engine integration | AI grade appears on signal cards |
| 5.3 | Gemini fallback provider | Auto-fallback if Claude is down |
| 5.4 | Learning loop — weekly accuracy analysis | AI self-corrects based on outcomes |
| 5.5 | Weekly report generation | Users see AI-generated performance report |
| 5.6 | Natural language query (optional) | "What tech stocks look good?" feature |

---

#### Phase 6: Polish & Launch (Week 11–12)

| Task | What | Deliverable |
|------|------|-------------|
| 6.1 | Landing page + pricing page (SSR, SEO) | Marketing pages for new users |
| 6.2 | Error handling & edge cases | Graceful failures, retry logic, offline states |
| 6.3 | Performance optimization | Lighthouse 90+, sub-second dashboard loads |
| 6.4 | Security audit | OWASP top 10 review, penetration test basics |
| 6.5 | Documentation — README, API docs, deployment guide | Anyone can set up and run the project |
| 6.6 | Cloud deployment (when ready) | Push to GCP Cloud Run + Supabase managed |

---

### Visual Timeline

```
Week  1  2  3  4  5  6  7  8  9  10  11  12
      ├──┴──┤  ├──┴──┤  ├──┤  ├──┴──┴──┤  ├──┴──┤  ├──┴──┤
      Phase 1   Phase 2   P3    Phase 4    Phase 5  Phase 6
      Foundation Signals  Risk   Web App   AI Agent  Polish
                          Shield
```

---

### Monthly Costs Summary (All Phases)

| Phase | Infrastructure | APIs | Total |
|-------|:-------------:|:----:|:-----:|
| **Development** (MacBook) | $0 (Docker) | $0 (free tiers) | **$0/mo** |
| **Beta** (10 users, single VPS) | $20 (VPS) + $25 (Supabase Pro) | ~$3 (Claude Haiku) | **~$48/mo** |
| **Growth** (1K users) | $50 (Cloud Run) + $25 (Supabase Pro) | ~$50 (Claude Sonnet) + $30 (Twelve Data) | **~$155/mo** |
| **Scale** (10K+ users) | $200 (Cloud Run scaled) + $599 (Supabase Team) | ~$100 (Claude) + $50 (data APIs) | **~$949/mo** |

---

### Summary: What We're Building

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│           TRADINGFIRM — SYSTEM E (HYBRID)                │
│                                                          │
│   5 Services   │  1 Database  │  1 Cache  │  1 AI       │
│                │              │           │              │
│   Data Engine  │  PostgreSQL  │  Redis    │  Claude      │
│   Signal Engine│  (Supabase)  │           │  (+ Gemini   │
│   Risk Shield  │              │           │   fallback)  │
│   Web App      │              │           │              │
│   AI Agent     │              │           │              │
│                │              │           │              │
│   12 weeks to MVP                                        │
│   $0/mo to build                                         │
│   Same containers: MacBook → Cloud                       │
│   Each service fails independently                       │
│   Swap data APIs in 1 env variable                       │
│   Swap AI provider in 1 env variable                     │
│                                                          │
└──────────────────────────────────────────────────────────┘
```
