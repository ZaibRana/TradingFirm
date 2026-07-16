/**
 * Yahoo Finance data fetching utilities (v4 API).
 * All Yahoo Finance calls go through here — single point for rate limiting and error handling.
 */
import YahooFinance from "yahoo-finance2";

// Single instance — v4 requires instantiation
const yf = new YahooFinance({ suppressNotices: ["yahooSurvey"] });

// Simple delay for rate limiting
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Fetch quote data for a list of symbols (price, change, volume, 52wk high/low)
 */
export async function getQuotes(symbols) {
  const results = [];
  for (const symbol of symbols) {
    try {
      const quote = await yf.quote(symbol);
      results.push({
        symbol: quote.symbol,
        name: quote.shortName || quote.longName || symbol,
        price: quote.regularMarketPrice,
        change: quote.regularMarketChange,
        changePercent: quote.regularMarketChangePercent,
        volume: quote.regularMarketVolume,
        avgVolume: quote.averageDailyVolume3Month,
        high52w: quote.fiftyTwoWeekHigh,
        low52w: quote.fiftyTwoWeekLow,
        marketCap: quote.marketCap,
        exchange: quote.exchange,
      });
      await delay(100); // Rate limiting
    } catch (err) {
      console.error(`Quote error for ${symbol}:`, err.message);
    }
  }
  return results;
}

/**
 * Fetch historical OHLCV chart data for a symbol.
 * @param {string} symbol - Ticker symbol
 * @param {string} interval - "1d", "1h", "5m" etc. (Note: "4h" not supported — use "1h" and aggregate)
 * @param {string} range - "5d", "1mo", "3mo", "6mo", "1y", "2y"
 */
export async function getChart(symbol, interval = "1d", range = "3mo") {
  try {
    const now = new Date();
    const rangeMap = {
      "5d": 5,
      "1mo": 30,
      "3mo": 90,
      "6mo": 180,
      "1y": 365,
      "2y": 730,
    };
    const daysBack = rangeMap[range] || 90;
    const period1 = new Date(now.getTime() - daysBack * 24 * 60 * 60 * 1000);

    const result = await yf.chart(symbol, {
      period1,
      interval,
    });

    if (!result?.quotes) return [];
    return result.quotes.map((q) => ({
      date: q.date,
      open: q.open,
      high: q.high,
      low: q.low,
      close: q.close,
      volume: q.volume,
    }));
  } catch (err) {
    console.error(`Chart error for ${symbol} (${interval}/${range}):`, err.message);
    return [];
  }
}

/**
 * Aggregate 1h candles into 4h candles.
 * Yahoo Finance doesn't support 4h interval natively.
 */
export function aggregate4h(hourlyCandles) {
  const chunks = [];
  for (let i = 0; i < hourlyCandles.length; i += 4) {
    const group = hourlyCandles.slice(i, i + 4);
    if (group.length === 0) continue;
    chunks.push({
      date: group[0].date,
      open: group[0].open,
      high: Math.max(...group.map((c) => c.high).filter(Boolean)),
      low: Math.min(...group.map((c) => c.low).filter(Boolean)),
      close: group[group.length - 1].close,
      volume: group.reduce((sum, c) => sum + (c.volume || 0), 0),
    });
  }
  return chunks;
}

/**
 * Fetch company profile (sector, industry, analyst ratings)
 */
export async function getProfile(symbol) {
  try {
    const result = await yf.quoteSummary(symbol, {
      modules: ["assetProfile", "recommendationTrend", "financialData"],
    });

    const profile = result.assetProfile || {};
    const financial = result.financialData || {};
    const recs = result.recommendationTrend?.trend || [];

    // Get latest analyst consensus
    const latestRec = recs.length > 0 ? recs[0] : {};
    const totalAnalysts =
      (latestRec.strongBuy || 0) +
      (latestRec.buy || 0) +
      (latestRec.hold || 0) +
      (latestRec.sell || 0) +
      (latestRec.strongSell || 0);

    let analystRating = "N/A";
    if (totalAnalysts > 0) {
      const buyish = (latestRec.strongBuy || 0) + (latestRec.buy || 0);
      const ratio = buyish / totalAnalysts;
      if (ratio >= 0.7) analystRating = "Strong Buy";
      else if (ratio >= 0.5) analystRating = "Buy";
      else if (ratio >= 0.3) analystRating = "Hold";
      else analystRating = "Sell";
    }

    return {
      sector: profile.sector || "Other",
      industry: profile.industry || "",
      fullTimeEmployees: profile.fullTimeEmployees,
      targetMeanPrice: financial.targetMeanPrice,
      analystRating,
      analystBreakdown: latestRec,
    };
  } catch (err) {
    console.error(`Profile error for ${symbol}:`, err.message);
    return { sector: "Other", industry: "", analystRating: "N/A" };
  }
}

/**
 * Fetch options data — call/put volumes for buyer interest
 */
export async function getOptionsInterest(symbol) {
  try {
    const result = await yf.options(symbol);
    const calls = result.options?.[0]?.calls || [];
    const puts = result.options?.[0]?.puts || [];

    const totalCallVolume = calls.reduce((sum, c) => sum + (c.volume || 0), 0);
    const totalPutVolume = puts.reduce((sum, p) => sum + (p.volume || 0), 0);
    const totalCallOI = calls.reduce(
      (sum, c) => sum + (c.openInterest || 0),
      0
    );
    const totalPutOI = puts.reduce(
      (sum, p) => sum + (p.openInterest || 0),
      0
    );

    const putCallRatio =
      totalCallVolume > 0 ? totalPutVolume / totalCallVolume : 999;

    return {
      callVolume: totalCallVolume,
      putVolume: totalPutVolume,
      callOpenInterest: totalCallOI,
      putOpenInterest: totalPutOI,
      putCallRatio: Math.round(putCallRatio * 100) / 100,
      bullishOptions: putCallRatio < 0.7 && totalCallVolume > 1000,
    };
  } catch (err) {
    console.error(`Options error for ${symbol}:`, err.message);
    return {
      callVolume: 0,
      putVolume: 0,
      putCallRatio: 0,
      bullishOptions: false,
    };
  }
}

/**
 * Get top gainers / most active from Yahoo screener.
 * Falls back to a curated watchlist if screener fails (e.g. weekends).
 */
export async function getScreenerStocks(scrId = "day_gainers", count = 40) {
  try {
    const result = await yf.screener({
      scrIds: scrId,
      count,
    });
    const quotes = result?.quotes || [];
    return quotes.map((q) => q.symbol).filter(Boolean);
  } catch (err) {
    console.error(`Screener error (${scrId}):`, err.message);
    return [];
  }
}

/**
 * Fallback: curated list of popular tradeable US stocks across sectors.
 * Used when screener is unavailable (weekends, holidays).
 */
export const FALLBACK_UNIVERSE = [
  // AI & Software ($10-$50 range candidates)
  "AI", "BBAI", "SOUN", "PATH", "S", "CFLT",
  // Chips & Semiconductors
  "INTC", "MU", "MCHP", "SWKS", "ON", "WOLF",
  // Quantum
  "IONQ", "RGTI", "QBTS", "QUBT", "ARQQ",
  // Energy / EV / Clean
  "NIO", "RIVN", "LCID", "PLUG", "CHPT", "BLNK",
  "BE", "RUN", "NOVA", "ENPH", "FSLR",
  // Robotics / Aerospace / Defense
  "RKLB", "JOBY", "ACHR", "LUNR", "RDW",
  // Health / Biotech
  "HIMS", "DNA", "EXAS", "IRTC", "SDGR",
  "CRSP", "BEAM", "EDIT",
  // Finance / Fintech
  "HOOD", "SOFI", "AFRM", "UPST", "MARA", "RIOT",
  // Telecom / Media
  "VOD", "LUMN", "WBD", "PARA",
  // Consumer
  "DASH", "LYFT", "SNAP", "PINS", "ETSY",
  // Industrial / Materials
  "SPCE", "STEM", "MP", "LAC", "ALB",
  // Other popular mid-priced
  "DKNG", "PLTR", "MSTR", "CLSK", "CIFR",
  "GRAB", "SE", "BIDU", "JD", "PDD",
];
