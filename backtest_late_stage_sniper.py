import sqlite3
import pandas as pd
import re
from datetime import datetime
import pytz
import numpy as np
import concurrent.futures

def parse_expiration_time(question_str: str) -> float:
    """
    Parses the question string to extract the expiration time.
    Example: "Bitcoin Up or Down - March 7, 3:35PM-3:40PM ET"
    Returns UTC Unix Epoch timestamp.
    """
    # Regex to capture Month, Day, and the End Time
    pattern = r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+\d{1,2}:\d{2}[A-Za-z]+-(\d{1,2}:\d{2}[A-Za-z]+)\s+(ET|EST|EDT)"
    match = re.search(pattern, question_str)
    
    if not match:
        return None
    
    month_str = match.group(1)
    day_str = match.group(2)
    end_time_str = match.group(3)
    
    # Construct a string we can parse
    time_str = f"2026 {month_str} {day_str} {end_time_str}"
    
    try:
        # Parse the datetime
        dt_naive = datetime.strptime(time_str, "%Y %B %d %I:%M%p")
        
        # Localize to Eastern Time (America/New_York handles DST automatically)
        eastern = pytz.timezone('America/New_York')
        dt_eastern = eastern.localize(dt_naive)
        
        # Convert to UTC and get unix timestamp
        dt_utc = dt_eastern.astimezone(pytz.utc)
        return dt_utc.timestamp()
    except ValueError:
        return None

def determine_winning_assets(df_trades: pd.DataFrame) -> set:
    """
    Determines the winning asset_ids based on a traded price of 1.0, 
    or the highest price near the end.
    """
    # Simply using trades where price reached 0.99 (or close to it)
    # Polymarket pays out at 1.0 upon resolution.
    winning_trades = df_trades[df_trades['price'] >= 0.99]
    return set(winning_trades['asset_id'].unique())


def calculate_metrics(trades: list) -> dict:
    if not trades:
        return {
            'Total Trades': 0, 'Win Rate': "0.0%", 
            'Avg Profit': 0.0, 'Max Consecutive Losses': 0
        }
        
    wins = sum(1 for t in trades if t['profit'] > 0)
    win_rate = wins / len(trades)
    avg_profit = sum(t['profit'] for t in trades) / len(trades)
    
    max_consecutive_losses = 0
    current_losses = 0
    
    for t in trades:
        if t['profit'] < 0:
            current_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
        else:
            current_losses = 0
            
    return {
        'Total Trades': len(trades),
        'Win Rate': f"{win_rate*100:.2f}%",
        'Avg Profit': round(avg_profit, 4),
        'Max Consecutive Losses': max_consecutive_losses
    }

def simulate_strategy(df_assets, df_bba, winning_assets, seconds_before_close, price_threshold):
    # Sort best_bid_ask by timestamp to use merge_asof or searchsorted efficiently if needed
    
    simulated_trades = []
    
    # Iterate through each unique asset
    for _, asset_row in df_assets.iterrows():
        asset_id = asset_row['asset_id']
        market_id = asset_row['market_id']
        exp_time = asset_row['expiration_time']
        
        if pd.isna(exp_time):
            continue
            
        target_timestamp = exp_time - seconds_before_close
        
        # Filter BBA for this asset
        market_bba = df_bba[df_bba['asset_id'] == asset_id]
        if market_bba.empty:
            continue
            
        # Find the snapshot closest to but not exceeding the target timestamp
        # Or just the absolute closest depending on interpretation. Let's find the closest before the target_timestamp
        eligible_bba = market_bba[market_bba['timestamp'] <= target_timestamp]
        
        if eligible_bba.empty:
            continue
            
        # Get the very last snapshot before our target execution time
        closest_snapshot = eligible_bba.iloc[-1]
        best_ask = closest_snapshot['best_ask']
        
        # Check entry condition
        if price_threshold <= best_ask <= 0.99:
            # We buy the shares at `best_ask`
            # If this asset won, it resolves to 1.0. If it lost, it resolves to 0.0
            is_winner = asset_id in winning_assets
            
            # Profit formulation:
            # Payout = 1.0 if won, else 0.0. Cost = best_ask. Profit = Payout - Cost
            payout = 1.0 if is_winner else 0.0
            profit = payout - best_ask
            
            simulated_trades.append({
                'market_id': market_id,
                'asset_id': asset_id,
                'target_timestamp': target_timestamp,
                'actual_timestamp': closest_snapshot['timestamp'],
                'buy_price': best_ask,
                'won': is_winner,
                'profit': profit
            })
            
    return simulated_trades

def _run_simulation_task(args):
    df_assets, df_bba, winning_assets, sec, thresh = args
    trades = simulate_strategy(df_assets, df_bba, winning_assets, sec, thresh)
    metrics = calculate_metrics(trades)
    
    trades_records = []
    for t in trades:
        t_record = t.copy()
        t_record['sec_before_close'] = sec
        t_record['price_threshold'] = thresh
        trades_records.append(t_record)
        
    res_dict = {
        'Sec Before Close': sec,
        'Price Threshold': thresh,
        **metrics
    }
    return res_dict, trades_records

def main():
    print("Loading data from sqlite db...")
    try:
        conn = sqlite3.connect('polymarket_bot.db')
        
        # Load tables
        df_markets = pd.read_sql_query("SELECT * FROM markets", conn)
        df_assets = pd.read_sql_query("SELECT * FROM market_assets", conn)
        df_bba = pd.read_sql_query("SELECT * FROM best_bid_ask", conn)
        df_trades = pd.read_sql_query("SELECT * FROM trades", conn)
        
        conn.close()
    except Exception as e:
        print(f"Error loading database: {e}")
        return

    print("Parsing expiration times...")
    df_markets['expiration_time'] = df_markets['title'].apply(parse_expiration_time)
    
    # Merge expiration time into assets
    df_assets = df_assets.merge(df_markets[['market_id', 'expiration_time']], on='market_id')
    
    # In BBA, the 'market_id' column is actually the 'asset_id' due to how it's stored. Rename it propery.
    df_bba = df_bba.rename(columns={'market_id': 'asset_id'})
    
    print("Determining winning assets...")
    winning_assets = determine_winning_assets(df_trades)
    
    # Sort BBA once to optimize
    df_bba = df_bba.sort_values(by='timestamp')

    # Grid search parameters
    seconds_options = [10, 20, 30, 45, 60]
    threshold_options = [0.75, 0.80, 0.85, 0.90, 0.95]
    
    tasks = []
    for sec in seconds_options:
        for thresh in threshold_options:
            tasks.append((df_assets, df_bba, winning_assets, sec, thresh))

    results = []
    all_trades_dataset = []

    print("\nStarting Grid Search with 4 subprocesses...")
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        future_to_task = {executor.submit(_run_simulation_task, task): task for task in tasks}
        
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            sec, thresh = task[3], task[4]
            try:
                res_dict, trades_records = future.result()
                results.append(res_dict)
                all_trades_dataset.extend(trades_records)
                print(f"Tested: {sec}s | Threshold: {thresh} -> Trades: {res_dict['Total Trades']}, Win Rate: {res_dict['Win Rate']}")
            except Exception as exc:
                print(f"Task {sec}s | Threshold: {thresh} generated an exception: {exc}")

    # Sort results to maintain deterministic output order
    results.sort(key=lambda x: (x['Sec Before Close'], x['Price Threshold']))

    # Formatted output
    df_results = pd.DataFrame(results)
    
    output_str = "=== Late-Stage Sniper Backtest Results ===\n" + df_results.to_markdown(index=False)
    print("\n" + output_str)
    
    with open("late_stage_sniper_results.txt", "w") as f:
        f.write(output_str)
        print("\nResults saved to late_stage_sniper_results.txt")
        
    if all_trades_dataset:
        df_all_trades = pd.DataFrame(all_trades_dataset)
        df_all_trades.to_csv("simulated_trades_dataset.csv", index=False)
        print("Detailed trades dataset saved to simulated_trades_dataset.csv for Monte Carlo simulation.")

if __name__ == "__main__":
    main()
