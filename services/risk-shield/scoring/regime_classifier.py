"""
TradingFirm — health score → regime (Part 3.3, spec decision 7).

HEALTHY ≥ 70, CAUTIOUS ≥ 40, DANGER ≥ 20, CRITICAL below. The strings are
the ones in the CHECK constraints of risk.health_checks and
risk.macro_briefs, so every layer speaks one vocabulary.
"""

from typing import Optional

HEALTHY = "HEALTHY"
CAUTIOUS = "CAUTIOUS"
DANGER = "DANGER"
CRITICAL = "CRITICAL"
REGIMES = (HEALTHY, CAUTIOUS, DANGER, CRITICAL)


def classify(score: Optional[int]) -> Optional[str]:
    """None → None (no score, no regime). Anything but an int 0–100
    (bool excluded) raises ValueError."""
    if score is None:
        return None
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError(f"health score must be an int in 0–100, got {score!r}")
    if score >= 70:
        return HEALTHY
    if score >= 40:
        return CAUTIOUS
    if score >= 20:
        return DANGER
    return CRITICAL
