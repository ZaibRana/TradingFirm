"""
TradingFirm — yfinance + Finviz Data Provider

Development data provider using free APIs.
Implements the DataProvider interface with:
  - Finviz for broad universe screening
  - yfinance for OHLCV data and stock info

All blocking I/O is wrapped in asyncio.to_thread().

⚠️  Dev only — not for commercial use. Swap to FMP provider for production.

RATE LIMIT RULES:
  - Finviz: 1-2.5s random delay between screener calls
  - yfinance download: batch max 50 tickers, 2s between batches
  - yfinance Ticker.info: 2s delay between calls (done in scanner)
  - Max 3 retries with exponential backoff
"""

import asyncio
import gc
import logging
import random
import time
from typing import Any, Optional

import pandas as pd
import yfinance as yf
from finvizfinance.screener.overview import Overview

from providers.base import DataProvider
from scanners.market_status import get_market_status

logger = logging.getLogger(__name__)

# ── Safety Constants ─────────────────────────────────────────────
FINVIZ_DELAY_MIN = 1.0       # Minimum seconds between Finviz calls
FINVIZ_DELAY_MAX = 2.5       # Maximum seconds between Finviz calls
YF_BATCH_SIZE = 20            # Max tickers per yf.download() call
YF_BATCH_DELAY = 3.0          # Seconds between download batches
YF_INFO_DELAY = 1.5           # Seconds between Ticker.info calls
MAX_RETRIES = 3               # Max retries for API calls
RETRY_BACKOFF = [1, 2, 4]     # Backoff schedule in seconds


class YFinanceProvider(DataProvider):
    """Data provider using yfinance (OHLCV) + Finviz (screening)."""

    # ── Version Guardrail ─────────────────────────────────────────
    # If someone installs a different major version, the provider
    # refuses to start. Prevents silent use of deprecated APIs.
    EXPECTED_YF_MAJOR = 1  # Code written for yfinance 1.x

    def __init__(self):
        """Initialize provider with version validation.

        NOTE: yfinance 1.x manages its own sessions, cookies, and
        User-Agent headers internally. Do NOT pass a custom session —
        it fights with yfinance's internal cookie/rate-limit handling
        and can actually make rate limiting worse.
        """
        # ── GUARDRAIL: Crash on major version mismatch ──
        installed = tuple(int(x) for x in yf.__version__.split(".")[:2])
        if installed[0] != self.EXPECTED_YF_MAJOR:
            raise RuntimeError(
                f"VERSION MISMATCH: yfinance {yf.__version__} installed, "
                f"but this code is written for {self.EXPECTED_YF_MAJOR}.x. "
                f"Breaking changes likely exist. Check parameters (session=, "
                f"Ticker.info vs fast_info, column format) before proceeding. "
                f"Update EXPECTED_YF_MAJOR after verifying compatibility."
            )
        logger.info(f"YFinanceProvider: yfinance {yf.__version__} (expected {self.EXPECTED_YF_MAJOR}.x) ✓")

    @property
    def provider_name(self) -> str:
        return "yfinance"

    # ── Screening ────────────────────────────────────────────────

    async def get_candidates(
        self,
        price_min: float = 10.0,
        price_max: float = 40.0,
    ) -> tuple[list[str], str]:
        """
        Pre-screen via Finviz: exchanges × cap sizes, then apply
        exact price filter in code. Returns (tickers, market_status).
        """
        return await asyncio.to_thread(
            self._get_candidates_sync, price_min, price_max
        )

    def _get_candidates_sync(
        self,
        price_min: float,
        price_max: float,
    ) -> tuple[list[str], str]:
        """
        Synchronous Finviz screening logic.
        Includes random delays between calls to avoid IP blocks.
        """
        status, et = get_market_status()
        logger.info(
            f"Screening candidates: {status} "
            f"({et.strftime('%a %I:%M %p ET')}), "
            f"price ${price_min}-${price_max}"
        )

        screener = Overview()
        exchanges = ["NASDAQ", "NYSE", "AMEX"]
        cap_sizes = [
            "Small ($300mln to $2bln)",
            "Mid ($2bln to $10bln)",
            "Large ($10bln to $200bln)",
        ]

        # Use broadest Finviz price floor
        if price_min >= 20:
            finviz_price = "Over $20"
        elif price_min >= 10:
            finviz_price = "Over $10"
        else:
            finviz_price = "Over $5"

        all_dfs = []
        call_count = 0
        for exchange in exchanges:
            for cap in cap_sizes:
                # ⚠️ RATE LIMIT: random delay between Finviz calls
                if call_count > 0:
                    delay = random.uniform(FINVIZ_DELAY_MIN, FINVIZ_DELAY_MAX)
                    logger.debug(f"  Finviz throttle: sleeping {delay:.1f}s")
                    time.sleep(delay)
                call_count += 1

                filters = {
                    "Exchange": exchange,
                    "Price": finviz_price,
                    "Average Volume": "Over 1M",
                    "Market Cap.": cap,
                }
                try:
                    screener.set_filter(filters_dict=filters)
                    df = screener.screener_view()
                    if df is not None and len(df) > 0:
                        all_dfs.append(df)
                        cap_name = cap.split("(")[0].strip()
                        logger.debug(f"  {exchange} {cap_name}: {len(df)}")
                except Exception as e:
                    cap_name = cap.split("(")[0].strip()
                    logger.warning(f"  {exchange} {cap_name}: Error — {e}")

        if not all_dfs:
            logger.warning("No candidates found from Finviz")
            return [], status

        combined = pd.concat(all_dfs, ignore_index=True)

        # Apply exact price filter
        if price_min:
            combined = combined[combined["Price"] >= price_min]
        if price_max:
            combined = combined[combined["Price"] <= price_max]

        combined = combined.drop_duplicates(subset=["Ticker"])
        raw_tickers = combined["Ticker"].tolist()

        # ⚠️ FIX: finvizfinance 1.3.0 doubles the first character of each ticker
        # e.g. "AAAL" → "AAL", "AABNB" → "ABNB"
        tickers = []
        fixed_count = 0
        for t in raw_tickers:
            if len(t) > 1 and t[0] == t[1]:
                tickers.append(t[1:])
                fixed_count += 1
            else:
                tickers.append(t)
        if fixed_count > 0:
            logger.warning(
                f"  Fixed {fixed_count}/{len(raw_tickers)} corrupted tickers "
                f"(finvizfinance first-char doubling bug)"
            )

        tickers = sorted(tickers)

        # ⚠️ GARBAGE: clean up Finviz DataFrames
        del all_dfs, combined
        gc.collect()

        logger.info(f"Finviz screening: {len(tickers)} candidates (${price_min}-${price_max})")
        return tickers, status

    # ── OHLCV Downloads ──────────────────────────────────────────

    async def download_daily(
        self,
        tickers: list[str],
        period: str = "1y",
    ) -> Any:
        """Bulk download daily candles via yfinance (batched, threaded)."""
        logger.info(f"Downloading daily data for {len(tickers)} tickers ({period})")
        return await asyncio.to_thread(
            self._download_batched_sync, tickers, period, "1d"
        )

    async def download_hourly(
        self,
        tickers: list[str],
        period: str = "3mo",
    ) -> Any:
        """Bulk download hourly candles via yfinance (batched, threaded)."""
        logger.info(f"Downloading hourly data for {len(tickers)} tickers ({period})")
        return await asyncio.to_thread(
            self._download_batched_sync, tickers, period, "1h"
        )

    def _download_batched_sync(
        self,
        tickers: list[str],
        period: str,
        interval: str,
    ) -> pd.DataFrame:
        """
        Synchronous yfinance bulk download with batch splitting and canary validation.

        CANARY BATCH: The first batch is treated as a "canary" — if it fails
        or returns empty data, we abort the entire download immediately.
        This prevents burning API calls when Yahoo is rate-limiting us.
        """
        if len(tickers) <= YF_BATCH_SIZE:
            return self._download_single_batch(tickers, period, interval)

        # Split into batches of YF_BATCH_SIZE
        batches = [
            tickers[i:i + YF_BATCH_SIZE]
            for i in range(0, len(tickers), YF_BATCH_SIZE)
        ]
        logger.info(
            f"  Splitting {len(tickers)} tickers into {len(batches)} batches "
            f"(max {YF_BATCH_SIZE} per batch, {YF_BATCH_DELAY}s delay)"
        )

        all_data = []
        for batch_num, batch in enumerate(batches):
            if batch_num > 0:
                logger.debug(f"  Batch {batch_num + 1}/{len(batches)}: sleeping {YF_BATCH_DELAY}s")
                time.sleep(YF_BATCH_DELAY)

            try:
                df = self._download_single_batch(batch, period, interval)
            except Exception as e:
                if batch_num == 0:
                    # ⚠️ CANARY FAILED: first batch errored — abort everything
                    logger.error(
                        f"  CANARY BATCH FAILED: First batch of {len(batch)} tickers "
                        f"raised an error: {e}. ABORTING all downloads. "
                        f"Yahoo may be rate-limiting — wait 15-30 minutes."
                    )
                    return pd.DataFrame()
                else:
                    logger.warning(
                        f"  Batch {batch_num + 1}/{len(batches)} failed: {e}. "
                        f"Skipping batch and continuing."
                    )
                    continue

            # ⚠️ CANARY CHECK: if first batch returns empty, abort
            if batch_num == 0 and (df is None or df.empty):
                logger.error(
                    f"  CANARY BATCH FAILED: First batch returned no data for "
                    f"{batch[:3]}... ABORTING all downloads. "
                    f"Yahoo may be rate-limiting — wait 15-30 minutes."
                )
                return pd.DataFrame()

            if df is not None and not df.empty:
                all_data.append(df)
                logger.info(
                    f"  Batch {batch_num + 1}/{len(batches)}: "
                    f"{len(batch)} tickers downloaded ✓"
                )
            else:
                logger.warning(
                    f"  Batch {batch_num + 1}/{len(batches)}: "
                    f"{len(batch)} tickers returned empty data"
                )

        if not all_data:
            return pd.DataFrame()

        # Concatenate batch results
        result = pd.concat(all_data, axis=1)

        # ⚠️ GARBAGE: clean up batch DataFrames
        del all_data
        gc.collect()

        return result

    def _download_single_batch(
        self,
        tickers: list[str],
        period: str,
        interval: str,
    ) -> pd.DataFrame:
        """Download a single batch of tickers.

        NOTE: No session= parameter — yfinance 1.x manages its own
        sessions and cookies. Passing a custom session interferes with
        yfinance's internal rate-limit and cookie handling.
        """
        ticker_str = " ".join(tickers)
        df = yf.download(
            ticker_str,
            period=period,
            interval=interval,
            group_by="ticker",
            threads=False,       # Sequential — avoids 50 parallel connections
            progress=False,
        )
        logger.debug(f"  Downloaded {interval} batch: {df.shape if df is not None else 'None'}")
        return df

    # ── Ticker Extraction ────────────────────────────────────────

    def extract_ticker_df(
        self,
        bulk_data: Any,
        ticker: str,
    ) -> Optional[pd.DataFrame]:
        """Extract one ticker's OHLCV from a grouped bulk DataFrame.

        Handles two yf.download() column formats:
          - Multi-ticker: MultiIndex columns grouped by ticker
          - Single-ticker: Flat columns ['Open','High','Low','Close','Volume']
        """
        try:
            if bulk_data is None or bulk_data.empty:
                return None

            # Check if columns are MultiIndex (multi-ticker download)
            if isinstance(bulk_data.columns, pd.MultiIndex):
                if ticker in bulk_data.columns.get_level_values(0):
                    df = bulk_data[ticker].dropna(subset=["Close"])
                    return df if len(df) > 0 else None
            else:
                # Single-ticker download: flat columns, data IS the ticker
                if "Close" in bulk_data.columns:
                    df = bulk_data.dropna(subset=["Close"])
                    return df if len(df) > 0 else None
        except Exception:
            pass
        return None

    # ── Stock Info / Enrichment ───────────────────────────────────

    async def get_stock_info(self, ticker: str, retries: int = MAX_RETRIES) -> dict:
        """
        Fetch enrichment data via yfinance Ticker API with retry.
        Max 3 retries with exponential backoff (1s, 2s, 4s).
        """
        last_error = None
        for attempt in range(retries):
            try:
                return await asyncio.to_thread(self._get_stock_info_sync, ticker)
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    wait = RETRY_BACKOFF[attempt]
                    logger.warning(
                        f"get_stock_info({ticker}) attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
        raise last_error

    def _get_stock_info_sync(self, ticker: str) -> dict:
        """Synchronous stock info fetch.

        Uses fast_info for reliable price/cap data (stable in yfinance 1.x),
        falls back to info for sector/industry/news (less stable).
        No session= parameter — yfinance 1.x manages its own.
        """
        t = yf.Ticker(ticker)

        # fast_info: reliable for market cap (yfinance 1.x)
        try:
            fi = t.fast_info
            market_cap = int(getattr(fi, "market_cap", 0) or 0)
        except Exception:
            market_cap = 0

        # info: less stable, but needed for sector/industry/float
        try:
            info = t.info or {}
        except Exception:
            info = {}

        # Float display string
        flt = info.get("floatShares", 0) or 0
        if flt >= 1e9:
            flt_str = f"{flt / 1e9:.1f}B"
        elif flt >= 1e6:
            flt_str = f"{flt / 1e6:.0f}M"
        elif flt > 0:
            flt_str = f"{flt:,.0f}"
        else:
            flt_str = "N/A"

        # Use fast_info market_cap if info didn't have it
        if not market_cap:
            market_cap = info.get("marketCap", 0) or 0

        # News headlines (top 3)
        headlines = []
        try:
            for item in (t.news or [])[:3]:
                if not isinstance(item, dict):
                    continue
                c = item.get("content", {}) or {}
                title = c.get("title", "") or item.get("title", "")
                if not title:
                    continue

                url = ""
                canon = c.get("canonicalUrl")
                if isinstance(canon, dict):
                    url = canon.get("url", "")
                if not url:
                    url = item.get("link", "")

                publisher = ""
                prov = c.get("provider")
                if isinstance(prov, dict):
                    publisher = prov.get("displayName", "")
                if not publisher:
                    publisher = item.get("publisher", "")

                headlines.append({
                    "title": title,
                    "url": url,
                    "publisher": publisher,
                })
        except Exception:
            pass

        return {
            "name": info.get("shortName", ""),
            "sector": info.get("sector", "Other"),
            "industry": info.get("industry", "Other"),
            "marketCap": market_cap,
            "floatShares": flt,
            "floatStr": flt_str,
            "news": headlines,
        }
