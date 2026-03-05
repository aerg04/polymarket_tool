import sqlite3
import math
from datetime import datetime

DB_NAME = "polymarket_bot.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def calculate_std_dev(data):
    n = len(data)
    if n < 2:
        return 0.0
    mean = sum(data) / n
    variance = sum((x - mean) ** 2 for x in data) / (n - 1)
    return math.sqrt(variance)

def analyze_trades():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bot_trades ORDER BY timestamp ASC")
    trades_rows = cursor.fetchall()
    conn.close()

    trades = [dict(row) for row in trades_rows]

    # Constants (Assumed for calculation purposes and percentage reporting)
    STARTING_BALANCE = 1000.0

    # Classify trades
    # Closed if status is KEYWORD 'CLOSED' or realized_pnl is not zero (handle potential float errors)
    # The user mentioned "simulated_open", so we only count realized gains/losses as closed
    closed_trades = [t for t in trades if t['status'] == 'CLOSED' or abs(float(t.get('realized_pnl') or 0)) > 0.000001]
    
    # Open trades: Everything else
    open_trades_list = [t for t in trades if t not in closed_trades]
    
    num_closed = len(closed_trades)
    num_open = len(open_trades_list)

    if num_closed == 0:
        print("Total Closed Trades\n0")
        print(f"Open Trades\n{num_open}")
        return

    # Extract Data from Closed Trades
    pnls = [float(t.get('realized_pnl') or 0) for t in closed_trades]
    sizes = [float(t.get('size_usd') or 0) for t in closed_trades]
    outcomes = [t.get('outcome', '') for t in closed_trades]
    timestamps = [t['timestamp'] for t in closed_trades]
    
    # 1. Total Net Profit
    total_pnl = sum(pnls)
    finishing_balance = STARTING_BALANCE + total_pnl
    profit_pct = (total_pnl / STARTING_BALANCE) * 100

    # 2. Drawdown
    # Calculate running balance starting from STARTING_BALANCE
    current_balance = STARTING_BALANCE
    peak_balance = STARTING_BALANCE
    max_drawdown = 0.0
    
    for pnl in pnls:
        current_balance += pnl
        if current_balance > peak_balance:
            peak_balance = current_balance
        drawdown = current_balance - peak_balance
        if drawdown < max_drawdown:
            max_drawdown = drawdown

    # 3. Annual Return
    if timestamps:
        start_ts = min(timestamps)
        end_ts = max(timestamps)
        duration_sec = end_ts - start_ts
        duration_days = duration_sec / 86400.0
        
        # Avoid exploding metric for tiny durations
        if duration_days > 0.04: # > 1 hour
            annual_return_pct = (profit_pct / duration_days) * 365
        else:
            annual_return_pct = 0.0
    else:
        annual_return_pct = 0.0
        
    # 4. Win/Loss Stats
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    total_wins = len(wins)
    total_losses = len(losses)
    
    avg_win = sum(wins) / total_wins if total_wins > 0 else 0.0
    avg_loss = abs(sum(losses) / total_losses) if total_losses > 0 else 0.0
    
    ratio_win_loss = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    win_rate = (total_wins / num_closed) * 100
    
    # 5. Expectancy
    if num_closed > 0:
        prob_win = total_wins / num_closed
        prob_loss = total_losses / num_closed
        expectancy = (prob_win * avg_win) - (prob_loss * avg_loss)
        
        avg_size = sum(sizes) / num_closed if num_closed > 0 else 1.0
        if avg_size > 0:
            expectancy_pct = (expectancy / avg_size) * 100
        else:
            expectancy_pct = 0.0
    else:
        expectancy = 0
        expectancy_pct = 0
    
    # 6. Long/Short (Assuming 'Yes' is Long, 'No' is Short)
    num_longs = outcomes.count('Yes')
    num_shorts = outcomes.count('No')
    long_pct = (num_longs / num_closed) * 100
    short_pct = (num_shorts / num_closed) * 100
    
    # 7. Ratios (Sharpe, Sortino, Calmar, Omega)
    # Group by date for daily stats
    daily_pnls = {}
    for t in closed_trades:
        date_str = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d')
        daily_pnls[date_str] = daily_pnls.get(date_str, 0.0) + float(t.get('realized_pnl', 0) or 0)
    
    daily_returns_vals = list(daily_pnls.values())
    
    # Daily returns as percentage of STARTING_BALANCE
    daily_ret_pcts = [(val / STARTING_BALANCE) for val in daily_returns_vals]
    
    sharpe = 0.0
    sortino = 0.0
    
    if daily_ret_pcts:
        mean_ret = sum(daily_ret_pcts) / len(daily_ret_pcts)
        std_ret = calculate_std_dev(daily_ret_pcts)
        
        # Sharpe (Annualized)
        if std_ret > 0:
            sharpe = (mean_ret / std_ret) * math.sqrt(365)
            
        # Sortino (Annualized)
        squared_neg_diffs = sum(min(0, r)**2 for r in daily_ret_pcts) / len(daily_ret_pcts)
        downside_dev = math.sqrt(squared_neg_diffs)
            
        if downside_dev > 0:
            sortino = (mean_ret / downside_dev) * math.sqrt(365)

    # Calmar Ratio: Annual Return % / Max Drawdown % (absolute)
    max_dd_pct = abs(max_drawdown) / STARTING_BALANCE
    if max_dd_pct > 0:
        calmar = (annual_return_pct / 100) / max_dd_pct
    else:
        calmar = 0.0
        
    # Omega Ratio: Sum(Wins) / Sum(Losses)
    sum_wins = sum(wins)
    sum_losses = abs(sum(losses))
    if sum_losses > 0:
        omega = sum_wins / sum_losses
    else:
        omega = float('inf') if sum_wins > 0 else 0.0
        
    # Streaks based on trade sequence (not time)
    curr_win = 0; max_win = 0
    curr_loss = 0; max_loss = 0
    
    for p in pnls:
        if p > 0:
            curr_win += 1
            curr_loss = 0
            if curr_win > max_win: max_win = curr_win
        else:
            curr_loss += 1
            curr_win = 0
            if curr_loss > max_loss: max_loss = curr_loss
            
    largest_win = max(pnls) if pnls else 0
    largest_loss = min(pnls) if pnls else 0

    # --- PRINT OUTPUT ---
    def fmt_money(val): return f"{val:.2f}"
    def fmt_pct(val): return f"{val:.2f}%"

    print("Total Closed Trades")
    print(f"{num_closed}")
    
    print("Total Net Profit")
    print(f"{fmt_money(total_pnl)} ({fmt_pct(profit_pct)})")
    
    print("Starting => Finishing Balance")
    print(f"{STARTING_BALANCE:.2f} => {fmt_money(finishing_balance)}")
    
    print("Open Trades")
    print(f"{num_open}")
    
    print("Total Paid Fees")
    print("0.00")
    
    print("Max Drawdown")
    print(f"{fmt_money(max_drawdown)}")
    
    print("Annual Return")
    print(f"{fmt_pct(annual_return_pct)}")
    
    print("Expectancy")
    print(f"{fmt_money(expectancy)} ({fmt_pct(expectancy_pct)})")
    
    print("Avg Win | Avg Loss")
    print(f"{fmt_money(avg_win)} | {fmt_money(avg_loss)}")
    
    print("Ratio Avg Win / Avg Loss")
    print(f"{ratio_win_loss:.2f}")
    
    print("Win-rate")
    print(f"{fmt_pct(win_rate)}")
    
    print("Longs | Shorts")
    print(f"{fmt_pct(long_pct)} | {fmt_pct(short_pct)}")
    
    print("Avg Holding Time")
    print("0 days, 0 hours, 0 minutes (N/A)")
    
    print("Winning Trades Avg Holding Time")
    print("0 days, 0 hours, 0 minutes (N/A)")
    
    print("Losing Trades Avg Holding Time")
    print("0 days, 0 hours, 0 minutes (N/A)")
    
    print("Sharpe Ratio")
    print(f"{sharpe:.2f}")
    
    print("Calmar Ratio")
    print(f"{calmar:.2f}")
    
    print("Sortino Ratio")
    print(f"{sortino:.2f}")
    
    print("Omega Ratio")
    if omega == float('inf'):
        print("Inf")
    else:
        print(f"{omega:.2f}")
    
    print("Winning Streak")
    print(f"{max_win}")
    
    print("Losing Streak")
    print(f"{max_loss}")
    
    print("Largest Winning Trade")
    print(f"{fmt_money(largest_win)}")
    
    print("Largest Losing Trade")
    print(f"{fmt_money(largest_loss)}")
    
    print("Total Winning Trades")
    print(f"{total_wins}")
    
    print("Total Losing Trades")
    print(f"{total_losses}")

if __name__ == "__main__":
    analyze_trades()
