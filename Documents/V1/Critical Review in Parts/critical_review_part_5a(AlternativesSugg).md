# Critical System Review — Part 5a of 7
## 🔄 Data Source Alternatives — Researched & Verified

For each data gap identified in Part 4, here are real alternatives with confirmed availability, pricing, and honest assessment.

---

### Gap 1: Options Flow (Currently: NOTHING → always empty `[]`)

**The problem:** `unusual_options_strikes` has no data source.

| Alternative | What It Provides | API? | Cost | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Unusual Whales** | Pre-classified unusual sweeps, blocks, dark pool prints, congressional trades | ✅ REST + WebSocket + Kafka. 100+ endpoints. OpenAPI spec available. | Pro/API tier required (retail plan ~$50/mo doesn't include API). Contact for API pricing. | 🟢 **Best option.** Professional-grade, well-documented, AI-friendly (MCP server available). |
| **FlowAlgo** | Options flow + dark pool alerts | ⚠️ Limited API, primarily web dashboard | $99/mo Pro | 🟡 Good data but weak API — would require scraping (risky). |
| **Tradier** | Raw option chains (quotes, greeks) — NOT unusual flow detection | ✅ REST API | Free tier available | 🟡 We'd have to BUILD our own unusual detection logic from raw chain data. |
| **CBOE LiveVol** | Professional options analytics | ✅ API | $100+/mo | 🟡 Overkill for our use case. |
| **Build our own from IBKR** | Snapshot option chains every 5 min, flag volume > 5x OI | ✅ Via IBKR API | Free (uses IBKR connection) | 🟡 Possible but complex — adds 10+ IBKR requests per scan cycle. Approximate results. |

**Recommendation:**
- **v1:** Remove `unusual_options_strikes` entirely. Don't pretend.
- **v2 (if budget allows):** Integrate Unusual Whales API. Their OpenAPI spec + MCP server makes integration straightforward.
- **v2 (budget-free):** Build DIY detector from IBKR option chains. Lower quality but free.

---

### Gap 2: News Sentiment (Currently: NONE — we only check calendar events)

**The problem:** "News Agent" doesn't read news.

| Alternative | What It Provides | API? | Cost | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Finnhub News** | Company news headlines with basic sentiment score | ✅ `/company-news` endpoint | Free tier (60 req/min) | 🟢 **Already available in our stack!** We use Finnhub but don't call this endpoint. |
| **Benzinga** | Real-time news feed with sentiment, analyst ratings | ✅ REST API | $99/mo (Basic) | 🟡 Good quality but costly for what we need. |
| **Alpha Vantage News Sentiment** | News articles with AI-generated sentiment scores | ✅ REST API | Free (but 25 calls/DAY — unusable for real-time) | 🔴 Rate limit too restrictive for intraday use. |
| **EODHD** | News with sentiment for stocks | ✅ REST API | $20/mo | 🟡 Budget option with decent quality. |
| **Google News RSS** | Headlines only, no sentiment scoring | ⚠️ RSS (no official API) | Free | 🔴 No API, no sentiment, fragile. |

**Recommendation:**
- **v1 (Free):** Add Finnhub `/company-news` endpoint — we already have the API key! Fetch headlines for watchlist tickers every 15 minutes. Display on dashboard. Don't use for signal scoring yet.
- **v2:** Add simple keyword-based sentiment (count positive vs negative words in headlines). Crude but free.
- **v3 (paid):** Integrate Benzinga for professional-grade sentiment if the system proves profitable enough to justify $99/mo.

---

### Gap 3: Better Market Data Alternative to Finnhub

**The problem:** Finnhub free tier is adequate but limited (60 req/min, delayed quotes for some symbols).

| Alternative | Real-Time? | Rate Limit | Cost | API Quality | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Finnhub (current)** | ⚠️ 15-min delay on some symbols (free) | 60/min | Free | Good | 🟢 Keep for macro data |
| **Alpaca Markets** | ✅ Real-time (IEX exchange only on free) | 200/min | Free (paper account) | Excellent | 🟢 **Best free alternative for quotes** |
| **Polygon.io** | ✅ Low latency (~25ms) | 5/min (free) | Free tier very limited. Paid: $29+/mo | Excellent | 🟡 Free tier too restrictive |
| **Alpha Vantage** | ⚠️ NASDAQ-licensed but delayed | 25/DAY (free) | Free (unusable), $50/mo (standard) | Good | 🔴 25 calls/day is unusable |
| **IBKR (current)** | ✅ Real-time (our primary source) | Local socket, generous | Included with account | Good | 🟢 Already our primary — keep it |

**Recommendation:**
- **Keep IBKR as primary** for real-time price data ✅
- **Keep Finnhub for macro** (VIX, economic calendar, company news) ✅
- **Consider Alpaca as backup** if Finnhub free tier changes terms. Alpaca's 200 req/min free tier is generous, and they support WebSocket streaming.
- **Don't add Polygon or Alpha Vantage** — their free tiers are too restrictive.

---

### Gap 4: Faster SEC Filing Detection

**The problem:** Our 10-minute EDGAR poll is slow. Professionals detect in seconds.

| Alternative | Speed After EDGAR Publishes | API? | Cost | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Our EDGAR EFTS poll (current)** | Up to 10 minutes | ✅ REST | Free | 🟡 Adequate for informational alerts |
| **SEC EDGAR RSS Feed** | ~1-2 minutes | ✅ RSS/Atom | Free | 🟢 **Free upgrade** — poll RSS every 60 seconds instead of EFTS every 10 min |
| **Quiver Quantitative** | ~30 seconds | ✅ REST API | $10-25/mo | 🟢 Good speed for low cost |
| **SEC EDGAR WebSocket (XBRL)** | ~5-15 seconds | ⚠️ Experimental | Free | 🟡 Fastest free option but complex to implement |
| **Unusual Whales (insider data)** | ~30 seconds | ✅ API | Included in API tier | 🟢 If already paying for options flow, get insider data too |

**Recommendation:**
- **v1 (Free upgrade):** Switch from EFTS polling every 10 min → EDGAR RSS feed polling every 60 seconds. Same data, ~10x faster detection, still free.
- **v2 (Paid):** If you subscribe to Unusual Whales for options flow, their insider trading endpoint covers SEC filings too — two gaps solved with one subscription.

---

### Gap 5: Gold & DXY Real-Time Data

**The problem:** Finnhub free tier may not include commodity/forex quotes.

| Alternative | Provides Gold? | Provides DXY? | Cost | Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **IBKR (GLD ETF + UUP ETF)** | ✅ Via GLD quote | ✅ Via UUP quote | Free (existing connection) | 🟢 **Use ETF proxies from IBKR** |
| **Finnhub forex** | ⚠️ May require paid tier | ⚠️ May require paid tier | Varies | 🟡 Don't rely on |
| **Drop them entirely** | N/A | N/A | Free | 🟢 **Recommended for v1** — low relevance for 5-min stock trading |

**Recommendation:**
- **v1:** Drop gold and DXY from signal inputs. Make them display-only on the dashboard if available from IBKR ETF proxies.
- They add complexity without meaningful 5-minute edge.

---

## Part 5a Summary: Data Source Upgrade Path

| Gap | v1 (Free) | v2 (Low Cost) | v3 (Paid) |
| :--- | :--- | :--- | :--- |
| Options flow | Remove field | DIY from IBKR chains | Unusual Whales API |
| News sentiment | Add Finnhub `/company-news` | Keyword-based sentiment | Benzinga ($99/mo) |
| Market data | Keep IBKR + Finnhub | Add Alpaca as backup | Polygon.io ($29/mo) |
| SEC detection | EDGAR RSS (60s poll) | Quiver Quant ($10-25/mo) | Unusual Whales (bundled) |
| Gold / DXY | Drop from signals, display-only | IBKR ETF proxies | N/A |

> **Bottom line:** The single highest-value paid upgrade is **Unusual Whales** — it closes both the options flow gap AND the SEC filing speed gap in one subscription. For v1, every gap can be improved for free by using data sources we already have but aren't calling (Finnhub `/company-news`, EDGAR RSS, IBKR ETF proxies).
