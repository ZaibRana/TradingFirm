/**
 * Multi-timeframe technical filters.
 * Checks 4h and 1h MACD, EMA crossovers, and ADX conditions.
 */
import { calcMACD, calcEMA, calcADX } from "./technicals";

/**
 * Check if MACD histogram is positive (bullish momentum).
 * Looks at the last completed candle's histogram value.
 */
function isMACDBullish(closes) {
  const { histogram } = calcMACD(closes, 12, 26, 9);
  const validHist = histogram.filter((v) => !isNaN(v));
  if (validHist.length < 2) return { pass: false, reason: "Insufficient MACD data" };

  const last = validHist[validHist.length - 1];
  const prev = validHist[validHist.length - 2];

  // Histogram is positive AND rising (or just crossed above zero)
  if (last > 0) {
    return {
      pass: true,
      reason: `Histogram +${last.toFixed(3)} (rising: ${last > prev ? "yes" : "no"})`,
      value: last,
      rising: last > prev,
    };
  }

  // Histogram is negative but rising toward zero (momentum shifting)
  if (last > prev && last > -0.1) {
    return {
      pass: true,
      reason: `Histogram ${last.toFixed(3)} rising toward zero`,
      value: last,
      rising: true,
    };
  }

  return {
    pass: false,
    reason: `Histogram ${last.toFixed(3)} (bearish)`,
    value: last,
    rising: last > prev,
  };
}

/**
 * Check if EMA 9 has crossed above EMA 21 (buy signal).
 */
function isEMACrossed(closes) {
  const ema9 = calcEMA(closes, 9);
  const ema21 = calcEMA(closes, 21);

  if (ema9.length < 3 || ema21.length < 3) {
    return { pass: false, reason: "Insufficient EMA data" };
  }

  const lastIdx = ema9.length - 1;
  const curr9 = ema9[lastIdx];
  const curr21 = ema21[lastIdx];
  const prev9 = ema9[lastIdx - 1];
  const prev21 = ema21[lastIdx - 1];

  if (isNaN(curr9) || isNaN(curr21)) {
    return { pass: false, reason: "EMA not calculable" };
  }

  // EMA 9 is above EMA 21
  if (curr9 > curr21) {
    // Check if cross happened recently (within last 3 bars)
    const recentCross = !isNaN(prev9) && !isNaN(prev21) && prev9 <= prev21;
    return {
      pass: true,
      reason: recentCross
        ? "EMA 9/21 just crossed ↑ (fresh)"
        : `EMA 9 > EMA 21 (gap: ${(curr9 - curr21).toFixed(3)})`,
      freshCross: recentCross,
    };
  }

  return {
    pass: false,
    reason: `EMA 9 < EMA 21 (gap: ${(curr9 - curr21).toFixed(3)})`,
    freshCross: false,
  };
}

/**
 * Check if 1h ADX slope is upward and higher than previous 2 readings.
 */
function isADXRising(candles) {
  const adx = calcADX(candles, 14);

  if (adx.length < 3) {
    return { pass: false, reason: "Insufficient ADX data", value: 0 };
  }

  const last = adx[adx.length - 1];
  const prev1 = adx[adx.length - 2];
  const prev2 = adx[adx.length - 3];

  // ADX must be rising: current > prev1 > prev2
  if (last > prev1 && prev1 > prev2) {
    return {
      pass: true,
      reason: `ADX ${last.toFixed(1)} ↑ (${prev2.toFixed(1)} → ${prev1.toFixed(1)} → ${last.toFixed(1)})`,
      value: last,
    };
  }

  // ADX is high but not strictly rising — still useful if > 25
  if (last > 25 && last > prev1) {
    return {
      pass: true,
      reason: `ADX ${last.toFixed(1)} (strong trend, partially rising)`,
      value: last,
    };
  }

  return {
    pass: false,
    reason: `ADX ${last.toFixed(1)} not rising (${prev2.toFixed(1)} → ${prev1.toFixed(1)} → ${last.toFixed(1)})`,
    value: last,
  };
}

/**
 * Check 1h volume vs average — is current activity above normal?
 */
function isVolumeConfirming(candles) {
  if (!candles || candles.length < 20) {
    return { pass: true, reason: "Insufficient volume data" };
  }

  const recent = candles.slice(-20);
  const avgVol =
    recent.reduce((sum, c) => sum + (c.volume || 0), 0) / recent.length;
  const lastVol = candles[candles.length - 1]?.volume || 0;

  if (avgVol === 0) return { pass: true, reason: "No volume data" };

  const ratio = lastVol / avgVol;

  if (ratio >= 1.2) {
    return {
      pass: true,
      reason: `Volume ${ratio.toFixed(1)}x avg (confirming)`,
      ratio,
    };
  }

  if (ratio >= 0.5) {
    return {
      pass: true,
      reason: `Volume ${ratio.toFixed(1)}x avg (normal)`,
      ratio,
    };
  }

  return {
    pass: false,
    reason: `Volume ${ratio.toFixed(1)}x avg (weak)`,
    ratio,
  };
}

/**
 * Run all technical filters on a stock.
 * Requires 1h candles and 4h candles (aggregated from 1h).
 */
export function runTechnicalFilters(hourlyCandles, fourHourCandles) {
  const hourlyCloses = hourlyCandles
    .map((c) => c.close)
    .filter((v) => v != null);
  const fourHourCloses = fourHourCandles
    .map((c) => c.close)
    .filter((v) => v != null);

  // 4h: MACD upwards + EMA crossed
  const macd4h = isMACDBullish(fourHourCloses);
  const ema4h = isEMACrossed(fourHourCloses);

  // 1h: MACD + EMA + ADX rising
  const macd1h = isMACDBullish(hourlyCloses);
  const ema1h = isEMACrossed(hourlyCloses);
  const adx1h = isADXRising(hourlyCandles);

  const filters = {
    "4h_macd": macd4h,
    "4h_ema": ema4h,
    "1h_macd": macd1h,
    "1h_ema": ema1h,
    "1h_adx": adx1h,
  };

  return {
    passed: Object.values(filters).every((f) => f.pass),
    filters,
  };
}
