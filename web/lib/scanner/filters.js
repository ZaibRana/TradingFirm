/**
 * Stock filter criteria.
 * Each filter function returns { pass: boolean, reason: string }
 */
import { calcATR, isUptrend, isBearishWeakening } from "./technicals";

/**
 * Filter: Price must be between $10 and $50.
 */
export function filterPriceRange(quote) {
  const price = quote.price || 0;
  if (price < 10) {
    return { pass: false, reason: `Price $${price.toFixed(2)} < $10` };
  }
  if (price > 50) {
    return { pass: false, reason: `Price $${price.toFixed(2)} > $50` };
  }
  return { pass: true, reason: `$${price.toFixed(2)} in range` };
}

/**
 * Filter: ATR must be >= $1.00 AND >= 1.5% of price.
 * Ensures the stock moves enough for 5m day trading.
 */
export function filterATR(quote, dailyCandles) {
  const atr = calcATR(dailyCandles, 14);
  const atrPercent = quote.price > 0 ? (atr / quote.price) * 100 : 0;

  if (atr < 1.0) {
    return { pass: false, reason: `ATR $${atr} < $1.00`, atr, atrPercent };
  }
  if (atrPercent < 1.5) {
    return {
      pass: false,
      reason: `ATR ${atrPercent.toFixed(1)}% < 1.5%`,
      atr,
      atrPercent,
    };
  }
  return { pass: true, reason: "OK", atr, atrPercent };
}

/**
 * Filter: Average daily volume must be >= 500K.
 * Low volume = wide spreads, hard to enter/exit.
 */
export function filterVolume(quote) {
  const avgVol = quote.avgVolume || 0;
  if (avgVol < 500_000) {
    return {
      pass: false,
      reason: `Avg vol ${(avgVol / 1000).toFixed(0)}K < 500K`,
    };
  }
  return { pass: true, reason: "OK" };
}

/**
 * Filter: Not at 52-week peak or low.
 * Reject if within 5% of either extreme.
 */
export function filterNot52wExtreme(quote) {
  const { price, high52w, low52w } = quote;
  if (!high52w || !low52w || high52w === low52w) {
    return { pass: true, reason: "No 52w data" };
  }

  const range = high52w - low52w;
  const fromHigh = ((high52w - price) / range) * 100;
  const fromLow = ((price - low52w) / range) * 100;

  if (fromHigh < 5) {
    return { pass: false, reason: `Within ${fromHigh.toFixed(1)}% of 52w high` };
  }
  if (fromLow < 5) {
    return { pass: false, reason: `Within ${fromLow.toFixed(1)}% of 52w low` };
  }
  return { pass: true, reason: "OK" };
}

/**
 * Filter: Not in a downtrend.
 * Price must be above 50-day EMA and EMA slope must be positive.
 */
export function filterUptrend(dailyCandles) {
  const trending = isUptrend(dailyCandles);
  if (!trending) {
    return { pass: false, reason: "Below 50 EMA or EMA falling" };
  }
  return { pass: true, reason: "OK" };
}

/**
 * Filter: 1D bearish candles shrinking (selling pressure weakening).
 */
export function filterBearishWeakening(dailyCandles) {
  const weakening = isBearishWeakening(dailyCandles);
  if (!weakening) {
    return { pass: false, reason: "Bearish pressure not weakening" };
  }
  return { pass: true, reason: "OK" };
}

/**
 * Run all basic filters on a stock.
 * Returns { passed: boolean, filters: { name: {pass, reason} }, enriched data }
 */
export function runBasicFilters(quote, dailyCandles) {
  const priceResult = filterPriceRange(quote);
  const atrResult = filterATR(quote, dailyCandles);
  const volumeResult = filterVolume(quote);
  const extremeResult = filterNot52wExtreme(quote);
  const trendResult = filterUptrend(dailyCandles);
  const bearishResult = filterBearishWeakening(dailyCandles);

  const filters = {
    priceRange: priceResult,
    atr: atrResult,
    volume: volumeResult,
    not52wExtreme: extremeResult,
    uptrend: trendResult,
    bearishWeakening: bearishResult,
  };

  // Must pass ALL filters
  const passed = Object.values(filters).every((f) => f.pass);

  // Count how many passed (for partial scoring)
  const passCount = Object.values(filters).filter((f) => f.pass).length;
  const totalFilters = Object.keys(filters).length;

  return {
    passed,
    passCount,
    totalFilters,
    filters,
    atr: atrResult.atr || 0,
    atrPercent: atrResult.atrPercent || 0,
  };
}
