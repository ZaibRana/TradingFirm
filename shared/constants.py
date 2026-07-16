"""
TradingFirm — Shared Constants

Constants shared across all services. Import from here
rather than hardcoding magic values in each service.
"""

# ── Service Configuration ──
SERVICE_PORTS = {
    "data-engine": 8001,
    "signal-engine": 8002,
    "risk-shield": 8003,
    "ai-agent": 8004,
    "web": 3000,
}

# ── Signal Status Lifecycle ──
SIGNAL_STATUSES = [
    "PENDING",
    "TRIGGERED",
    "HIT_TP1",
    "HIT_TP2",
    "HIT_TP3",
    "STOPPED_OUT",
    "EXPIRED",
    "ADJUSTED",
]

# ── Risk Regimes ──
RISK_REGIMES = ["HEALTHY", "CAUTIOUS", "DANGER", "CRITICAL"]

# ── Signal Directions ──
DIRECTIONS = ["LONG", "SHORT"]

# ── Market Sessions ──
MARKET_SESSIONS = ["pre_market", "market_open", "after_hours", "closed"]

# ── Redis Channels ──
REDIS_CHANNELS = {
    "scan_complete": "tf:scan:complete",
    "signal_new": "tf:signal:new",
    "signal_update": "tf:signal:update",
    "health_update": "tf:risk:health",
    "alert": "tf:risk:alert",
}

# ── Redis Cache Keys ──
REDIS_CACHE_KEYS = {
    "last_scan": "tf:cache:last_scan",
    "market_health": "tf:cache:market_health",
    "watchlist": "tf:cache:watchlist",
}

# ── Cache TTLs (seconds) ──
CACHE_TTL = {
    "scan_results": 3600,      # 1 hour
    "market_health": 300,      # 5 minutes
    "stock_data": 1800,        # 30 minutes
    "indicators": 900,         # 15 minutes
}
