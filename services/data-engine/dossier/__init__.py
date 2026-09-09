"""
TradingFirm — Dossier assembly (Part 2.4). Spec: docs/specs/2.4.md.

One document per ticker: the Part 1.7 indicator snapshot and zones, Part
2.1 news / events / recommendations / profile, Part 2.2 filings, Part 2.3
earnings reactions. Every section carries its own `status`, so a source
that is down degrades one section instead of failing the request.

`HORIZON_PROFILES` is the one place a holding horizon changes the windows;
`horizon` also rides in the cache key, so Phase 6's intraday mode is a
second row here rather than branches through the assembly.
"""

HORIZON_SWING = "swing"

HORIZON_PROFILES = {
    HORIZON_SWING: {
        "news_days": 7,
        "filing_days": 30,
        "filing_forms": ("8-K", "4"),
        "reactions": 8,
        "events_window_days": 120,
        "bars_interval": "1d",
    },
}

# Size caps (plan row 2.4). Applied after sorting, never before.
MAX_HEADLINES = 30
MAX_FILINGS = 10

__all__ = [
    "HORIZON_SWING",
    "HORIZON_PROFILES",
    "MAX_HEADLINES",
    "MAX_FILINGS",
]
