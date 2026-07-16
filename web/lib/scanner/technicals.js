/**
 * Technical indicator calculations.
 * Pure math — no API calls. Takes OHLCV arrays, returns numbers.
 */

/**
 * Calculate ATR (Average True Range).
 * Measures average daily price movement in dollars.
 * @param {Array} candles - OHLCV candles [{high, low, close}, ...]
 * @param {number} period - Lookback period (default 14)
 * @returns {number} ATR value in dollars
 */
export function calcATR(candles, period = 14) {
  if (!candles || candles.length < period + 1) return 0;

  const trueRanges = [];
  for (let i = 1; i < candles.length; i++) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevClose = candles[i - 1].close;
    if (high == null || low == null || prevClose == null) continue;

    const tr = Math.max(
      high - low,
      Math.abs(high - prevClose),
      Math.abs(low - prevClose)
    );
    trueRanges.push(tr);
  }

  if (trueRanges.length < period) return 0;

  // Wilder's smoothed ATR
  let atr = trueRanges.slice(0, period).reduce((s, v) => s + v, 0) / period;
  for (let i = period; i < trueRanges.length; i++) {
    atr = (atr * (period - 1) + trueRanges[i]) / period;
  }

  return Math.round(atr * 100) / 100;
}

/**
 * Calculate EMA (Exponential Moving Average).
 * @param {Array} values - Array of numbers (e.g., close prices)
 * @param {number} period - EMA period
 * @returns {Array} EMA values (same length as input, early values are NaN)
 */
export function calcEMA(values, period) {
  if (!values || values.length < period) return [];

  const k = 2 / (period + 1);
  const ema = new Array(values.length).fill(NaN);

  // Seed with SMA
  let sum = 0;
  for (let i = 0; i < period; i++) {
    sum += values[i];
  }
  ema[period - 1] = sum / period;

  // Calculate EMA
  for (let i = period; i < values.length; i++) {
    ema[i] = values[i] * k + ema[i - 1] * (1 - k);
  }

  return ema;
}

/**
 * Calculate MACD (Moving Average Convergence Divergence).
 * @param {Array} closes - Array of close prices
 * @param {number} fast - Fast EMA period (default 12)
 * @param {number} slow - Slow EMA period (default 26)
 * @param {number} signal - Signal EMA period (default 9)
 * @returns {Object} { macdLine, signalLine, histogram } — arrays
 */
export function calcMACD(closes, fast = 12, slow = 26, signal = 9) {
  if (!closes || closes.length < slow + signal) {
    return { macdLine: [], signalLine: [], histogram: [] };
  }

  const emaFast = calcEMA(closes, fast);
  const emaSlow = calcEMA(closes, slow);

  // MACD Line = EMA(fast) - EMA(slow)
  const macdLine = emaFast.map((f, i) => {
    if (isNaN(f) || isNaN(emaSlow[i])) return NaN;
    return f - emaSlow[i];
  });

  // Signal Line = EMA(9) of MACD Line
  const validMacd = macdLine.filter((v) => !isNaN(v));
  const signalEma = calcEMA(validMacd, signal);

  // Rebuild signal line aligned with original indices
  const signalLine = new Array(macdLine.length).fill(NaN);
  let validIdx = 0;
  for (let i = 0; i < macdLine.length; i++) {
    if (!isNaN(macdLine[i])) {
      signalLine[i] = signalEma[validIdx] || NaN;
      validIdx++;
    }
  }

  // Histogram = MACD - Signal
  const histogram = macdLine.map((m, i) => {
    if (isNaN(m) || isNaN(signalLine[i])) return NaN;
    return m - signalLine[i];
  });

  return { macdLine, signalLine, histogram };
}

/**
 * Calculate ADX (Average Directional Index).
 * Measures trend strength (0-100).
 * @param {Array} candles - OHLCV candles
 * @param {number} period - ADX period (default 14)
 * @returns {Array} ADX values
 */
export function calcADX(candles, period = 14) {
  if (!candles || candles.length < period * 2 + 1) return [];

  const plusDM = [];
  const minusDM = [];
  const tr = [];

  for (let i = 1; i < candles.length; i++) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevHigh = candles[i - 1].high;
    const prevLow = candles[i - 1].low;
    const prevClose = candles[i - 1].close;

    if ([high, low, prevHigh, prevLow, prevClose].some((v) => v == null)) {
      plusDM.push(0);
      minusDM.push(0);
      tr.push(0);
      continue;
    }

    const upMove = high - prevHigh;
    const downMove = prevLow - low;

    plusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);
    tr.push(
      Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose))
    );
  }

  // Wilder's smoothing
  const smooth = (arr, p) => {
    const result = [];
    let sum = arr.slice(0, p).reduce((s, v) => s + v, 0);
    result.push(sum);
    for (let i = p; i < arr.length; i++) {
      sum = sum - sum / p + arr[i];
      result.push(sum);
    }
    return result;
  };

  const smoothTR = smooth(tr, period);
  const smoothPlusDM = smooth(plusDM, period);
  const smoothMinusDM = smooth(minusDM, period);

  const dx = [];
  for (let i = 0; i < smoothTR.length; i++) {
    if (smoothTR[i] === 0) {
      dx.push(0);
      continue;
    }
    const plusDI = (smoothPlusDM[i] / smoothTR[i]) * 100;
    const minusDI = (smoothMinusDM[i] / smoothTR[i]) * 100;
    const diSum = plusDI + minusDI;
    dx.push(diSum === 0 ? 0 : (Math.abs(plusDI - minusDI) / diSum) * 100);
  }

  // ADX = Wilder's smoothed DX
  if (dx.length < period) return [];
  const adx = [];
  let adxVal = dx.slice(0, period).reduce((s, v) => s + v, 0) / period;
  adx.push(adxVal);
  for (let i = period; i < dx.length; i++) {
    adxVal = (adxVal * (period - 1) + dx[i]) / period;
    adx.push(adxVal);
  }

  return adx.map((v) => Math.round(v * 100) / 100);
}

/**
 * Check if price is in an uptrend.
 * Condition: price above 50-period EMA and EMA slope is positive.
 */
export function isUptrend(candles) {
  if (!candles || candles.length < 55) return false;

  const closes = candles.map((c) => c.close).filter(Boolean);
  const ema50 = calcEMA(closes, 50);

  const currentPrice = closes[closes.length - 1];
  const currentEMA = ema50[ema50.length - 1];
  const prevEMA = ema50[ema50.length - 6]; // 5 bars ago

  if (isNaN(currentEMA) || isNaN(prevEMA)) return false;

  return currentPrice > currentEMA && currentEMA > prevEMA;
}

/**
 * Check if bearish candles are shrinking (selling pressure weakening).
 * Looks at the body size of bearish candles — last one should be smaller than avg of previous 3.
 */
export function isBearishWeakening(candles) {
  if (!candles || candles.length < 10) return false;

  // Get bearish candles (close < open)
  const bearish = candles
    .filter((c) => c.close != null && c.open != null && c.close < c.open)
    .slice(-4); // Last 4 bearish candles

  if (bearish.length < 4) return true; // Not enough bearish candles = bullish sign

  const lastBody = Math.abs(bearish[3].open - bearish[3].close);
  const prevAvg =
    bearish
      .slice(0, 3)
      .reduce((sum, c) => sum + Math.abs(c.open - c.close), 0) / 3;

  return lastBody < prevAvg;
}
