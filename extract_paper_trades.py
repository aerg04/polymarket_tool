import sqlite3
import pandas as pd

def extract_paper_trades():
    conn = sqlite3.connect('data/polymarket_bot_backup2.db')
    query = "SELECT * FROM trades WHERE trade_source = 'PAPER' AND realized_pnl IS NOT NULL"
    df = pd.read_sql_query(query, conn)
    
    print(f"Found {len(df)} resolved paper trades. Exporting to CSV...")

    output_file = 'paper_trades3.csv'
    df.to_csv(output_file, index=False)
    print(f"Extracted {len(df)} paper trades to {output_file}")
    conn.close()

if __name__ == '__main__':
    extract_paper_trades()
