"""
Step 4: Enrich winning stocks with options interest, analyst rating, sector/industry.
Uses yfinance Ticker objects for detailed data.
"""
import yfinance as yf
import json
from datetime import datetime


def enrich_stock(ticker):
    """Fetch options interest, analyst rating, sector/industry for one ticker."""
    t = yf.Ticker(ticker)
    info = t.info or {}

    result = {
        "symbol": ticker,
        "name": info.get("shortName", ""),
        "price": info.get("currentPrice", info.get("regularMarketPrice", 0)),
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "marketCap": info.get("marketCap", 0),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh", 0),
        "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow", 0),
    }

    # Analyst rating
    result["analystRating"] = info.get("recommendationKey", "")
    result["analystTargetPrice"] = info.get("targetMeanPrice", 0)
    result["numberOfAnalysts"] = info.get("numberOfAnalystOpinions", 0)

    # Options interest — check nearest expiration
    try:
        expirations = t.options
        if expirations:
            nearest = expirations[0]
            chain = t.option_chain(nearest)
            calls = chain.calls
            puts = chain.puts

            total_call_vol = int(calls["volume"].sum()) if "volume" in calls else 0
            total_put_vol = int(puts["volume"].sum()) if "volume" in puts else 0
            total_call_oi = int(calls["openInterest"].sum()) if "openInterest" in calls else 0
            total_put_oi = int(puts["openInterest"].sum()) if "openInterest" in puts else 0

            pc_ratio = total_put_vol / total_call_vol if total_call_vol > 0 else 999

            result["options"] = {
                "expiration": nearest,
                "callVolume": total_call_vol,
                "putVolume": total_put_vol,
                "callOI": total_call_oi,
                "putOI": total_put_oi,
                "putCallRatio": round(pc_ratio, 2),
                "bullish": pc_ratio < 0.7 and total_call_vol > 500,
            }
        else:
            result["options"] = {"error": "No options available"}
    except Exception as e:
        result["options"] = {"error": str(e)}

    return result


def enrich_all(tickers):
    """Enrich all winning tickers."""
    enriched = []
    for t in tickers:
        print(f"  Enriching {t}...")
        data = enrich_stock(t)
        enriched.append(data)

        # Print summary
        opts = data.get("options", {})
        bull = "🟢 BULLISH" if opts.get("bullish") else "⚪ neutral"
        pcr = opts.get("putCallRatio", "N/A")
        print(f"    {data['name']} | {data['sector']} | {data['industry']}")
        print(f"    Analyst: {data['analystRating']} | Target: ${data['analystTargetPrice']}")
        print(f"    Options: P/C ratio {pcr} {bull} | Calls: {opts.get('callVolume', 0)} Puts: {opts.get('putVolume', 0)}")

    return enriched


if __name__ == "__main__":
    # Hardcode winners from step 3 for quick testing
    winners = ["DOCU", "PYPL", "CALX", "REZI", "RNG", "CMG"]
    print(f"Enriching {len(winners)} stocks...\n")
    results = enrich_all(winners)

    # Save to JSON
    output = {
        "timestamp": datetime.now().isoformat(),
        "count": len(results),
        "stocks": results,
    }
    with open("results.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n✅ Results saved to results.json")
