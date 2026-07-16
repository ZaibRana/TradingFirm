"""
TradingFirm — Market Status Detection

Determines current US market session based on Eastern Time.
Used to adjust scanner behavior (e.g., RVOL thresholds).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Market hours in minutes from midnight
MARKET_OPEN = 570    # 9:30 AM ET
MARKET_CLOSE = 960   # 4:00 PM ET
PREMARKET_START = 240  # 4:00 AM ET


def get_market_status() -> tuple[str, datetime]:
    """
    Determine current US stock market session.

    Returns:
        (status, eastern_time) where status is one of:
        - 'market_open'  : 9:30 AM – 4:00 PM ET weekdays
        - 'pre_market'   : before 9:30 AM ET weekdays
        - 'after_hours'  : after 4:00 PM ET weekdays
        - 'weekend'      : Saturday or Sunday
    """
    et = datetime.now(ET)

    # Weekend check (Saturday=5, Sunday=6)
    if et.weekday() >= 5:
        return "weekend", et

    mins = et.hour * 60 + et.minute

    if MARKET_OPEN <= mins < MARKET_CLOSE:
        return "market_open", et
    elif mins < MARKET_OPEN:
        return "pre_market", et
    else:
        return "after_hours", et


def get_minutes_since_open() -> int:
    """
    Minutes elapsed since market open (9:30 AM ET).
    Returns 0 if market is not open. Capped at 390 (full session).
    """
    _, et = get_market_status()
    mins = et.hour * 60 + et.minute
    elapsed = mins - MARKET_OPEN
    return max(0, min(elapsed, 390))
