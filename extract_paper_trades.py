import sqlite3
import pandas as pd
import requests
import json
import time

def extract_paper_trades():
    conn = sqlite3.connect('polymarket_bot_backup.db')
    query = "SELECT * FROM trades WHERE trade_source = 'PAPER'"
    df = pd.read_sql_query(query, conn)
    
    resolved_prices = []
    realized_pnls = []
    
    print(f"Found {len(df)} paper trades. Resolving markets via API...")
    
    for idx, row in df.iterrows():
        asset_id = row['asset_id']
        buy_price = row['price']
        market_id = row['market_id']
        
        # Fallback query if market_id is null on the trades table
        if not market_id:
            cursor = conn.cursor()
            cursor.execute("SELECT market_id FROM market_assets WHERE asset_id = ?", (asset_id,))
            res = cursor.fetchone()
            if res:
                market_id = res[0]

        if not market_id:
            resolved_prices.append(None)
            realized_pnls.append(None)
            continue

        res_price_found = None
        pnl_found = None
        
        try:
            resp = requests.get(f"https://gamma-api.polymarket.com/markets/{market_id}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("closed") and data.get("endDate"):
                    prices = data.get("outcomePrices", "[]")
                    tokens = data.get("clobTokenIds", "[]")
                    
                    if isinstance(prices, str): prices = json.loads(prices)
                    if isinstance(tokens, str): tokens = json.loads(tokens)
                    
                    if asset_id in tokens:
                        token_idx = tokens.index(asset_id)
                        if token_idx < len(prices):
                            res_price_found = float(prices[token_idx])
                            pnl_found = res_price_found - buy_price
        except Exception as e:
            print(f"Error fetching data for market {market_id}: {e}")

        resolved_prices.append(res_price_found)
        realized_pnls.append(pnl_found)
        time.sleep(0.1) # brief rate limit pause for Gamma API

    # Overwrite the columns directly with downloaded resolution data
    df['resolved_price'] = resolved_prices
    df['realized_pnl'] = realized_pnls

    output_file = 'paper_trades.csv'
    df.to_csv(output_file, index=False)
    print(f"Extracted and resolved {len(df)} paper trades to {output_file}")
    conn.close()

if __name__ == '__main__':
    extract_paper_trades()
