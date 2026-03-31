import sys
import os
import pandas as pd

# Add src to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data.market_data import UpbitMarketData

def test_bb_width_presence():
    print("Fetching OHLCV for KRW-BTC...")
    df = UpbitMarketData.get_ohlcv_with_indicators_new("KRW-BTC", count=50, interval="minutes/15")
    
    if df.empty:
        print("❌ Failed to fetch data.")
        return

    print(f"Columns available: {df.columns.tolist()}")
    
    if "bb_width" in df.columns:
        print("✅ 'bb_width' column is present.")
    else:
        print("❌ 'bb_width' column is MISSING.")

if __name__ == "__main__":
    test_bb_width_presence()
