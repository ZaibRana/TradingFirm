/**
 * API Route: /api/scanner/filter
 *
 * Stage 2: Price + Volume + ATR + 1D MACD/EMA.
 * ONLY the user's criteria. Nothing invented.
 */
import { NextResponse } from "next/server";
import { getChart } from "@/lib/data/yahoo";
import { calcATR, calcMACD, calcEMA } from "@/lib/scanner/technicals";

export async function POST(request) {
  try {
    const { stocks } = await request.json();

    if (!stocks || !Array.isArray(stocks) || stocks.length === 0) {
      return NextResponse.json(
        { success: false, error: "No stocks provided" },
        { status: 400 }
      );
    }

    const results = [];
    const rejected = [];

    for (const stock of stocks) {
      try {
        // Price $10-$50
        const price = stock.price || 0;
        if (price < 10 || price > 50) {
          rejected.push({ symbol: stock.symbol, reason: `Price $${price.toFixed(2)}` });
          continue;
        }

        // Basic volume (need enough liquidity to trade)
        if ((stock.avgVolume || 0) < 500_000) {
          rejected.push({ symbol: stock.symbol, reason: "Low volume" });
          continue;
        }

        // Fetch daily candles
        const dailyCandles = await getChart(stock.symbol, "1d", "3mo");
        if (dailyCandles.length < 30) {
          rejected.push({ symbol: stock.symbol, reason: "Insufficient data" });
          continue;
        }

        const closes = dailyCandles.map((c) => c.close);

        // ATR must be >= $1 (stock moves enough for day trading)
        const atr = calcATR(dailyCandles, 14);
        if (atr < 1.0) {
          rejected.push({ symbol: stock.symbol, reason: `ATR $${atr.toFixed(2)} too low` });
          continue;
        }

        // 1D MACD must be upwards (histogram positive or rising toward positive)
        const { histogram } = calcMACD(closes, 12, 26, 9);
        const validHist = histogram.filter((v) => !isNaN(v));
        if (validHist.length < 2) {
          rejected.push({ symbol: stock.symbol, reason: "MACD data insufficient" });
          continue;
        }
        const lastHist = validHist[validHist.length - 1];
        // 1D MACD histogram must be positive = upwards
        if (lastHist <= 0) {
          rejected.push({ symbol: stock.symbol, reason: `1D MACD negative (${lastHist.toFixed(3)})` });
          continue;
        }

        // 1D EMA 9 must be above EMA 21 (buy signal)
        const ema9 = calcEMA(closes, 9);
        const ema21 = calcEMA(closes, 21);
        const last9 = ema9[ema9.length - 1];
        const last21 = ema21[ema21.length - 1];
        if (isNaN(last9) || isNaN(last21) || last9 <= last21) {
          rejected.push({ symbol: stock.symbol, reason: "1D EMA not crossed for buy" });
          continue;
        }

        // Passed all checks
        results.push({ ...stock, atr });

      } catch (err) {
        rejected.push({ symbol: stock.symbol, reason: err.message });
      }
    }

    return NextResponse.json({
      success: true,
      timestamp: new Date().toISOString(),
      input: stocks.length,
      passed: results.length,
      rejected: rejected.length,
      stocks: results,
    });
  } catch (error) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}
