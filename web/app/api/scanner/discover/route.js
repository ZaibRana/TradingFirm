/**
 * API Route: /api/scanner/discover
 *
 * Stage 1: Wide scan. Fetches quotes for the entire stock universe
 * + screener results. Pre-filters by $10-$50 and volume >= 500K.
 */
import { NextResponse } from "next/server";
import { getScreenerStocks, getQuotes } from "@/lib/data/yahoo";
import { STOCK_UNIVERSE } from "@/lib/data/universe";

export async function GET() {
  try {
    // Fetch screener stocks (live movers) in parallel
    let screenerSymbols = [];
    try {
      const [gainers, actives, losers] = await Promise.all([
        getScreenerStocks("day_gainers", 50),
        getScreenerStocks("most_actives", 50),
        getScreenerStocks("day_losers", 50),
      ]);
      screenerSymbols = [...gainers, ...actives, ...losers];
    } catch {
      // Screener may fail on weekends — that's fine
    }

    // Combine screener + full universe, deduplicate
    const allSymbols = [...new Set([...screenerSymbols, ...STOCK_UNIVERSE])]
      .filter((s) => !s.includes(".") && !s.includes("-") && s.length <= 5);

    // Fetch quotes in batches of 50 (Yahoo rate limit friendly)
    const BATCH_SIZE = 50;
    let allQuotes = [];

    for (let i = 0; i < allSymbols.length; i += BATCH_SIZE) {
      const batch = allSymbols.slice(i, i + BATCH_SIZE);
      try {
        const quotes = await getQuotes(batch);
        allQuotes = allQuotes.concat(quotes);
      } catch (err) {
        console.error(`Batch ${i}-${i + BATCH_SIZE} error:`, err.message);
      }
    }

    // Pre-filter: $10-$50 price AND avg volume >= 500K
    const filtered = allQuotes.filter((q) => {
      const p = q.price || 0;
      const v = q.avgVolume || 0;
      return p >= 10 && p <= 50 && v >= 500_000;
    });

    return NextResponse.json({
      success: true,
      count: filtered.length,
      totalScanned: allQuotes.length,
      timestamp: new Date().toISOString(),
      stocks: filtered,
    });
  } catch (error) {
    console.error("Discovery error:", error);
    return NextResponse.json(
      { success: false, error: error.message },
      { status: 500 }
    );
  }
}
