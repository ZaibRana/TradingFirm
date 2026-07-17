"""
Step 1: Finviz Broad Filter
Scans all NASDAQ + NYSE stocks and filters to candidates using Finviz screener.
Output: list of tickers that pass basic criteria.
"""
from finvizfinance.screener.overview import Overview
import pandas as pd

def get_candidates():
    """Use Finviz to filter 6000+ stocks down to ~50-80 candidates."""
    screener = Overview()
    
    filters = {
        "Exchange": "NASDAQ",
        "Price": "Over $10",
        "Average Volume": "Over 1M",
        "Market Cap.": "Mid ($2bln to $10bln)",
        "IPO Date": "More than 5 years ago",
    }
    screener.set_filter(filters_dict=filters)
    nasdaq_mid = screener.screener_view()

    # Also get large cap NASDAQ
    filters["Market Cap."] = "Large ($10bln to $200bln)"
    screener.set_filter(filters_dict=filters)
    nasdaq_large = screener.screener_view()

    # NYSE mid cap
    filters["Exchange"] = "NYSE"
    filters["Market Cap."] = "Mid ($2bln to $10bln)"
    screener.set_filter(filters_dict=filters)
    nyse_mid = screener.screener_view()

    # NYSE large cap
    filters["Market Cap."] = "Large ($10bln to $200bln)"
    screener.set_filter(filters_dict=filters)
    nyse_large = screener.screener_view()

    # Combine all results
    dfs = [df for df in [nasdaq_mid, nasdaq_large, nyse_mid, nyse_large] if df is not None and len(df) > 0]
    
    if not dfs:
        print("No results from Finviz!")
        return []

    combined = pd.concat(dfs, ignore_index=True)
    
    # Filter price <= $50
    combined = combined[combined["Price"] <= 50.0]
    combined = combined.drop_duplicates(subset=["Ticker"])
    
    tickers = combined["Ticker"].tolist()
    
    print(f"Finviz scan complete:")
    print(f"  Total after $50 cap: {len(combined)}")
    print(f"  Sample: {', '.join(tickers[:20])}{'...' if len(tickers) > 20 else ''}")
    
    return tickers

if __name__ == "__main__":
    tickers = get_candidates()
    print(f"\nTotal candidates: {len(tickers)}")
