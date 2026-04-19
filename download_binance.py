import pandas as pd
import requests
import time

def get_binance_klines_extended(symbol, interval, start_ts, end_ts):
    all_klines = []
    current_start = start_ts
    limit = 1000
    interval_ms = 5 * 60 * 1000 
    
    print(f"Fetching {symbol} {interval} data from {pd.to_datetime(start_ts, unit='ms')} to {pd.to_datetime(end_ts, unit='ms')}...")

    while current_start < end_ts:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&startTime={current_start}&endTime={end_ts}&limit={limit}"
        response = requests.get(url)
        
        if response.status_code != 200:
            print(f"API Error: {response.text}")
            break
            
        data = response.json()
        if not data:
            break 
            
        all_klines.extend(data)
        last_open_time = data[-1][0]
        current_start = last_open_time + interval_ms
        time.sleep(0.2) 

    cols = ['open_time','open','high','low','close','volume','close_time','qav','num_trades','taker_base_vol','taker_quote_vol','ignore']
    df = pd.DataFrame(all_klines, columns=cols)
    
    for c in ['open','high','low','close']: 
        df[c] = df[c].astype(float)
        
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.drop_duplicates(subset=['open_time']).reset_index(drop=True)
    
    print(f"Successfully fetched {len(df)} candles for {symbol}.")
    return df[['open_time','open','high','low','close']]

if __name__ == "__main__":
    # 1. Load your trades to determine the exact time window needed
    trade_file = 'paper_trades3.csv'
    df_trades = pd.read_csv(trade_file)
    
    # Add a generous buffer: 200 candles before the first trade, 20 candles after the last
    min_ts = int(df_trades['timestamp'].min() * 1000) - (200 * 5 * 60 * 1000)
    max_ts = int(df_trades['timestamp'].max() * 1000) + (20 * 5 * 60 * 1000)

    # 2. Fetch the data
    btc = get_binance_klines_extended("BTCUSDT", "5m", min_ts, max_ts)

    # 3. Save to local CSV files
    btc.to_csv('btc_klines.csv', index=False)
    print("Market data securely saved to btc_klines.csv and eth_klines.csv.")