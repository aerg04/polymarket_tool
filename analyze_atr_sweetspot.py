import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def analyze_atr():
    # 1. Load the processed data
    print("Loading fully_processed_trades.csv...")
    try:
        df = pd.read_csv('fully_processed_trades1.csv')
    except FileNotFoundError:
        print("Error: Could not find fully_processed_trades.csv. Please ensure it was generated.")
        return

    # Drop any trades that lack an ATR value or PnL just in case
    df = df.dropna(subset=['atr', 'realized_pnl'])
    
    # 2. Test different ATR Max limits
    # We will simulate: "What if I ONLY took trades when ATR was less than X?"
    # We will compute the accumulated PnL for each possible X boundary based on our data.
    
    # Let's create an array of percentiles or exact values to test as thresholds
    min_atr = df['atr'].min()
    max_atr = df['atr'].max()
    
    # Create 100 even steps between the minimum and maximum ATR
    atr_thresholds = np.linspace(min_atr, max_atr, num=100)
    
    scenarios = []
    
    for atr_cap in atr_thresholds:
        # Filter trades that occurred below this ATR "ceiling"
        accepted_trades = df[df['atr'] <= atr_cap]
        
        # Calculate statistics
        pnl = accepted_trades['realized_pnl'].sum()
        total_trades = len(accepted_trades)
        wins = len(accepted_trades[accepted_trades['result'] == 'Win'])
        losses = len(accepted_trades[accepted_trades['sl_hit'] == 1])
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        scenarios.append({
            'atr_cap': atr_cap,
            'total_pnl': pnl,
            'trades_taken': total_trades,
            'win_rate_%': win_rate,
            'wins': wins,
            'losses': losses
        })
        
    results_df = pd.DataFrame(scenarios)
    
    # 3. Find the optimal or "sweet spot"
    best_overall = results_df.loc[results_df['total_pnl'].idxmax()]
    optimal_atr = best_overall['atr_cap']
    
    print("\n" + "="*50)
    print("🎯 OPTIMAL ATR THRESHOLD FOUND")
    print("="*50)
    print(f"Max Allowed ATR : {optimal_atr:.2f}")
    print(f"Total PnL Achieved : {best_overall['total_pnl']:.4f}")
    print(f"Win Rate : {best_overall['win_rate_%']:.2f}%")
    print(f"Trades Taken : {int(best_overall['trades_taken'])} (Wins: {int(best_overall['wins'])}, Losses: {int(best_overall['losses'])})")
    print("="*50)

    # Export the trades that fall within the sweet spot
    sweetspot_trades = df[df['atr'] <= optimal_atr]
    sweetspot_trades.to_csv('paper_trades_sweetspot.csv', index=False)
    print("\n[+] Saved filtered trades to 'paper_trades_sweetspot.csv' for Monte Carlo simulation.")

    # 4. Plot the PnL Curve vs ATR Limit
    plt.figure(figsize=(10, 6))
    plt.plot(results_df['atr_cap'], results_df['total_pnl'], color='blue', linewidth=2)
    plt.axvline(best_overall['atr_cap'], color='red', linestyle='--', linewidth=1.5,
                label=f"Sweet Spot (ATR ≤ {best_overall['atr_cap']:.2f})")
    
    plt.title('Accumulated Deal PnL vs. Maximum ATR Allowed')
    plt.xlabel('ATR Maximum Threshold (Only take trades when ATR ≤ X)')
    plt.ylabel('Realized PnL')
    plt.grid(alpha=0.3)
    plt.legend()
    
    plt.savefig('atr_pnl_sweetspot.png')
    print("\nPlot saved successfully to 'atr_pnl_sweetspot.png'")

if __name__ == "__main__":
    analyze_atr()
