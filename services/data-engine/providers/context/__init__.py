"""Context fetchers (Phase 2): Finnhub (2.1), SEC EDGAR (2.2). Each module
is pure HTTP + cache; storage goes through db.py. Import from the
submodules; `ratelimit.py` holds the limiter they share."""
