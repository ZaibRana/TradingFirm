/**
 * API Route: /api/scanner/technical
 *
 * Stage 3: 4h MACD/EMA + 1h MACD/EMA/ADX.
 * ONLY the user's criteria. Nothing invented.
 *
 * Weekend/holiday handling: Yahoo Finance automatically returns
 * last trading day data. We verify candles are present.
 */
import { NextResponse } from "next/server";
import { getChart, aggregate4h, getProfile } from "@/lib/data/yahoo";
import { calcMACD, calcEMA, calcADX } from "@/lib/scanner/technicals";
import { classifyFromYahoo } from "@/lib/scanner/sectors";

function checkMACDUp(closes) {
  const { histogram } = calcMACD(closes, 12, 26, 9);
  const valid = histogram.filter((v) => !isNaN(v));
  if (valid.length < 2) return { pass: false, reason: "Insufficient MACD data" };
  const last = valid[valid.length - 1];
  // Histogram must be positive = MACD is upwards
  if (last > 0) {
    return { pass: true, reason: `MACD +${last.toFixed(3)}` };
  }
  return { pass: false, reason: `MACD negative ${last.toFixed(3)}` };
}

function checkEMACross(closes) {
  const ema9 = calcEMA(closes, 9);
  const ema21 = calcEMA(closes, 21);
  if (ema9.length < 2 || ema21.length < 2) return { pass: false, reason: "Insufficient EMA data" };
  const last9 = ema9[ema9.length - 1];
  const last21 = ema21[ema21.length - 1];
  if (isNaN(last9) || isNaN(last21)) return { pass: false, reason: "EMA not calculable" };
  if (last9 > last21) {
    return { pass: true, reason: `EMA9 > EMA21 (gap ${(last9 - last21).toFixed(3)})` };
  }
  return { pass: false, reason: `EMA9 < EMA21` };
}

function checkADXRising(candles) {
  const adx = calcADX(candles, 14);
  if (adx.length < 3) return { pass: false, reason: "Insufficient ADX data" };
  const last = adx[adx.length - 1];
  const prev1 = adx[adx.length - 2];
  const prev2 = adx[adx.length - 3];
  // ADX current > previous 2
  if (last > prev1 && last > prev2) {
    return { pass: true, reason: `ADX ${last.toFixed(1)} rising` };
  }
  return { pass: false, reason: `ADX ${last.toFixed(1)} not rising` };
}

export async function POST(request) {
  try {
    const { stocks } = await request.json();
    if (!stocks?.length) {
      return NextResponse.json({ success: false, error: "No stocks" }, { status: 400 });
    }

    const results = [];
    const rejected = [];

    for (const stock of stocks) {
      try {
        const hourly = await getChart(stock.symbol, "1h", "1mo");
        if (hourly.length < 35) {
          rejected.push({ symbol: stock.symbol, reason: "Insufficient 1h data" });
          continue;
        }

        const fourHour = aggregate4h(hourly);
        if (fourHour.length < 35) {
          rejected.push({ symbol: stock.symbol, reason: "Insufficient 4h data" });
          continue;
        }

        const hourCloses = hourly.map((c) => c.close);
        const fourCloses = fourHour.map((c) => c.close);

        // 4h MACD upwards
        const macd4h = checkMACDUp(fourCloses);
        if (!macd4h.pass) { rejected.push({ symbol: stock.symbol, reason: `4h: ${macd4h.reason}` }); continue; }

        // 4h EMA crossed for buy
        const ema4h = checkEMACross(fourCloses);
        if (!ema4h.pass) { rejected.push({ symbol: stock.symbol, reason: `4h: ${ema4h.reason}` }); continue; }

        // 1h MACD upwards
        const macd1h = checkMACDUp(hourCloses);
        if (!macd1h.pass) { rejected.push({ symbol: stock.symbol, reason: `1h: ${macd1h.reason}` }); continue; }

        // 1h EMA crossed for buy
        const ema1h = checkEMACross(hourCloses);
        if (!ema1h.pass) { rejected.push({ symbol: stock.symbol, reason: `1h: ${ema1h.reason}` }); continue; }

        // 1h ADX rising (higher than previous 2)
        const adx1h = checkADXRising(hourly);
        if (!adx1h.pass) { rejected.push({ symbol: stock.symbol, reason: `1h: ${adx1h.reason}` }); continue; }

        // ALL passed — fetch sector info
        results.push({ ...stock });

      } catch (err) {
        rejected.push({ symbol: stock.symbol, reason: err.message });
      }
    }

    // Fetch sector for passing stocks
    const enriched = [];
    for (const stock of results) {
      try {
        const profile = await getProfile(stock.symbol);
        const { sectorId, sector } = classifyFromYahoo(profile.sector, profile.industry);
        enriched.push({ ...stock, sectorId, sector, industry: profile.industry || "", analystRating: profile.analystRating || "" });
      } catch {
        enriched.push({ ...stock, sectorId: "other", sector: "Other" });
      }
    }

    return NextResponse.json({
      success: true,
      timestamp: new Date().toISOString(),
      input: stocks.length,
      passed: enriched.length,
      rejected: rejected.length,
      stocks: enriched,
    });
  } catch (error) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}
