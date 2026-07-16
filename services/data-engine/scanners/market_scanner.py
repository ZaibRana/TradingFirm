"""
TradingFirm — Market Scanner Pipeline

Orchestrates the full scan flow:
  1. Pre-screen candidates (Finviz)
  2. Bulk download OHLCV data (yfinance)
  3. Apply technical filters (ATRP, RVOL, EMA, 52w)
  4. Enrich winners (name, sector, float, news)
  5. Apply fundamental gates (market cap, float)
  6. Sort by quality score (RVOL × ATRP)

Ported from scanner/pro_scan.py into an async, provider-agnostic class.

RATE LIMIT SAFETY:
  - 2s delay between enrichment calls (yf.Ticker.info)
  - Large DataFrames explicitly deleted after use
  - gc.collect() after heavy processing
"""

import asyncio
import gc
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from indicators.technical import (
    aggregate_4h,
    calc_atr,
    calc_atrp,
    calc_rvol,
    check_52w_position,
    ema,
)
from providers.base import DataProvider
from scanners.market_status import get_market_status, get_minutes_since_open
from scanners.models import ScanResult, StockResult, NewsItem

logger = logging.getLogger(__name__)

# ── Safety Constants ─────────────────────────────────────────────
ENRICHMENT_DELAY = 2.0  # Seconds between yf.Ticker.info calls


class MarketScanner:
    """
    Professional day trading scanner.

    Filters:
      1. Price $10–$40 (configurable)
      2. Market Cap > $500M
      3. Daily Volume > 1M (Finviz pre-filter)
      4. RVOL > 1.2 (live) or > 1.0 (weekend/premarket)
      5. ATRP 2.5%–6.0%
      6. 4H: Price > 50 EMA
      7. 1H: 20 EMA > 50 EMA
      8. Not at 52-week top/bottom 10%
      9. IPO > 6 months (120+ trading days of data)
     10. Float 20M–1B (enrichment gate)
    """

    def __init__(self, provider: DataProvider):
        self.provider = provider

    async def run_scan(
        self,
        price_min: float = 10.0,
        price_max: float = 40.0,
        advanced: bool = False,
    ) -> ScanResult:
        """
        Execute the full scan pipeline.

        Args:
            price_min: Minimum stock price
            price_max: Maximum stock price
            advanced: Enable 5-minute tradability filters (slower)

        Returns:
            ScanResult with all passing stocks, sorted by quality
        """
        t0 = time.time()
        logger.info(f"{'='*60}")
        logger.info(f"  SCAN STARTED — ${price_min}-${price_max} (advanced={advanced})")
        logger.info(f"{'='*60}")

        # ── Step 1: Pre-screen via provider ──
        logger.info("Step 1: Pre-screening candidates...")
        tickers, market_status = await self.provider.get_candidates(
            price_min=price_min,
            price_max=price_max,
        )

        if not tickers:
            logger.warning("No candidates found. Returning empty result.")
            return ScanResult(
                timestamp=datetime.now(timezone.utc),
                duration_seconds=time.time() - t0,
                market_status=market_status,
                total_scanned=0,
                passed_count=0,
                stocks=[],
            )

        logger.info(f"Step 1 complete: {len(tickers)} candidates")

        # ── Step 2: Bulk download ──
        logger.info("Step 2: Downloading chart data...")
        daily_data = await self.provider.download_daily(tickers)
        hourly_data = await self.provider.download_hourly(tickers)
        logger.info("Step 2 complete: Downloads finished")

        # ── Step 3: Apply filters ──
        logger.info(f"Step 3: Applying filters ({market_status} mode)...")
        winners = {}
        rejections = {}

        for ticker in tickers:
            daily_df = self.provider.extract_ticker_df(daily_data, ticker)
            hourly_df = self.provider.extract_ticker_df(hourly_data, ticker)

            if daily_df is None or hourly_df is None:
                continue

            passed, result = self._apply_filters(
                ticker, daily_df, hourly_df, market_status
            )

            if passed:
                winners[ticker] = result
                logger.debug(
                    f"  ✅ {ticker:6s}  ATRP {result['atrp']:>5.1f}%  "
                    f"RVOL {result['rvol']:>5.2f}x  52w {result['pos52w']:>3d}%"
                )
            else:
                # Track rejection reasons
                key = result.split("(")[0].split("<")[0].strip()
                rejections[key] = rejections.get(key, 0) + 1

        logger.info(f"Step 3 complete: {len(winners)} passed / {len(tickers)} scanned")
        for reason, count in sorted(rejections.items(), key=lambda x: -x[1])[:8]:
            logger.info(f"  {count:>4d}×  {reason}")

        # ⚠️ GARBAGE: release large DataFrames before enrichment
        del daily_data, hourly_data
        gc.collect()
        logger.debug("Released bulk DataFrames from memory")

        # ── Step 4: Enrich winners ──
        logger.info(f"Step 4: Enriching {len(winners)} winners (with {ENRICHMENT_DELAY}s delays)...")
        results = []
        enrich_count = 0

        for ticker, filter_data in winners.items():
            # ⚠️ RATE LIMIT: delay between yf.Ticker.info calls
            if enrich_count > 0:
                await asyncio.sleep(ENRICHMENT_DELAY)
            enrich_count += 1

            try:
                enriched = await self._enrich_stock(ticker, filter_data)
                if enriched is not None:
                    results.append(enriched)
                    logger.debug(
                        f"  ✅ {ticker:6s}  {enriched.name:<30s}  "
                        f"{enriched.industry:<25s}  Float: {enriched.float_str}"
                    )
            except Exception as e:
                logger.warning(f"  ❌ {ticker}: Enrichment failed — {e}")

        # ── Step 5: Sort by quality (RVOL × ATRP) ──
        results.sort(
            key=lambda s: (s.rvol or 0) * (s.atrp or 0),
            reverse=True,
        )

        duration = time.time() - t0
        logger.info(f"{'='*60}")
        logger.info(f"  SCAN COMPLETE: {len(results)} stocks in {duration:.1f}s")
        logger.info(f"{'='*60}")

        return ScanResult(
            timestamp=datetime.now(timezone.utc),
            duration_seconds=round(duration, 2),
            market_status=market_status,
            total_scanned=len(tickers),
            passed_count=len(results),
            stocks=results,
        )

    # ── Private: Filter Logic ────────────────────────────────────

    def _apply_filters(
        self,
        ticker: str,
        daily_df,
        hourly_df,
        market_status: str,
    ) -> tuple[bool, any]:
        """
        Apply all technical filters to a single stock.

        Returns:
            (True, data_dict) if passed, or (False, reason_string) if rejected.
        """
        closes = daily_df["Close"]

        # ── IPO / history check (120 trading days ≈ 6 months) ──
        if len(daily_df) < 120:
            return False, f"Too new ({len(daily_df)} days < 120)"

        # ── ATRP 2.5% – 6.0% ──
        atrp = calc_atrp(daily_df["High"], daily_df["Low"], closes)
        if np.isnan(atrp):
            return False, "ATR calc failed"
        if atrp < 2.5:
            return False, f"ATRP {atrp:.1f}% < 2.5%"
        if atrp > 6.0:
            return False, f"ATRP {atrp:.1f}% > 6%"

        # ── RVOL (time-adjusted for live market) ──
        if len(daily_df) < 22:
            return False, "Not enough days for RVOL"

        last_vol = float(daily_df["Volume"].iloc[-1])

        # Scale partial-day volume during market hours
        scale = 1.0
        if market_status == "market_open":
            mins_elapsed = get_minutes_since_open()
            mins_elapsed = max(mins_elapsed, 5)  # avoid divide-by-zero
            scale = 390.0 / mins_elapsed

        rvol = calc_rvol(daily_df["Volume"], last_vol, lookback=20, scale_factor=scale)

        rvol_floor = 1.0 if market_status in ("weekend", "pre_market") else 1.2
        if rvol < rvol_floor:
            return False, f"RVOL {rvol:.2f}"

        # ── 4H: Price > 50 EMA ──
        h4 = aggregate_4h(hourly_df)
        if len(h4) < 55:
            return False, f"4H data short ({len(h4)} bars)"

        ema50_4h = ema(h4["Close"], 50).iloc[-1]
        price_4h = h4["Close"].iloc[-1]
        if price_4h <= ema50_4h:
            return False, "4H Price < 50 EMA"

        # ── 1H: EMA 20 > EMA 50 ──
        if len(hourly_df) < 55:
            return False, f"1H data short ({len(hourly_df)} bars)"

        ema20_1h = ema(hourly_df["Close"], 20).iloc[-1]
        ema50_1h = ema(hourly_df["Close"], 50).iloc[-1]
        if ema20_1h <= ema50_1h:
            return False, "1H EMA20 < EMA50"

        # ── 52-week position: not in top/bottom 10% ──
        pos = check_52w_position(closes)
        if pos > 0.90:
            return False, f"52w high zone ({pos:.0%})"
        if pos < 0.10:
            return False, f"52w low zone ({pos:.0%})"

        # ── All passed ──
        atr_series = calc_atr(daily_df["High"], daily_df["Low"], closes)
        return True, {
            "atrp": round(atrp, 2),
            "atr": round(float(atr_series.iloc[-1]), 2),
            "rvol": round(rvol, 2),
            "pos52w": round(pos * 100),
            "price": round(float(closes.iloc[-1]), 2),
            "hi52": round(float(closes.max()), 2),
            "lo52": round(float(closes.min()), 2),
        }

    # ── Private: Enrichment ──────────────────────────────────────

    async def _enrich_stock(
        self,
        ticker: str,
        filter_data: dict,
    ) -> Optional[StockResult]:
        """
        Enrich a stock with fundamental data and apply final gates.

        Returns StockResult if it passes market cap and float gates,
        or None if rejected.
        """
        info = await self.provider.get_stock_info(ticker)

        # ── Market cap gate: > $500M ──
        market_cap = info.get("marketCap", 0) or 0
        if market_cap < 500_000_000:
            mc_str = f"${market_cap / 1e6:.0f}M" if market_cap > 0 else "N/A"
            logger.info(f"  ❌ {ticker:6s}  Market cap {mc_str} < $500M — rejected")
            return None

        # ── Float gate: 20M – 1B ──
        float_shares = info.get("floatShares", 0) or 0
        float_str = info.get("floatStr", "N/A")
        if 0 < float_shares < 20_000_000:
            logger.info(f"  ❌ {ticker:6s}  Float {float_str} < 20M — too illiquid")
            return None
        if float_shares > 1_000_000_000:
            logger.info(f"  ❌ {ticker:6s}  Float {float_str} > 1B — too slow")
            return None

        # ── Build result ──
        news_items = [
            NewsItem(
                title=n.get("title", ""),
                url=n.get("url", ""),
                publisher=n.get("publisher", ""),
            )
            for n in info.get("news", [])
        ]

        return StockResult(
            symbol=ticker,
            name=info.get("name", ""),
            price=filter_data["price"],
            sector=info.get("sector", "Other"),
            industry=info.get("industry", "Other"),
            market_cap=market_cap,
            float_shares=float_shares,
            float_str=float_str,
            fifty_two_week_high=filter_data["hi52"],
            fifty_two_week_low=filter_data["lo52"],
            atrp=filter_data["atrp"],
            atr=filter_data["atr"],
            rvol=filter_data["rvol"],
            pos_52w=filter_data["pos52w"],
            news=news_items,
        )
