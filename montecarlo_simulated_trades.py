import pandas as pd
import numpy as np
import argparse

def run_montecarlo(df, num_simulations=10000, trades_per_sim=1000, initial_capital=100.0, position_pct=0.10):
    """
    Run a Monte Carlo simulation on a dataframe of trades.
    Position sizing is calculated as a percentage of current capital.
    """
    results = []

    # Group by the specified parameters
    grouped = df.groupby(['sec_before_close', 'price_threshold'])

    for name, group in grouped:
        sec_before_close, price_threshold = name
        
        # Extract profits directly - assuming the profit column is per unit (e.g., per 1 share)
        profits_per_share = group['profit'].values
        buy_prices = group['buy_price'].values
        
        # Calculate ROI per trade (profit per dollar invested)
        # Avoid division by zero
        buy_prices_safe = np.where(buy_prices == 0, 1e-6, buy_prices)
        rois = profits_per_share / buy_prices_safe
        
        num_trades_in_group = len(group)
        if num_trades_in_group == 0:
            continue

        simulated_final_capitals = np.zeros(num_simulations)
        simulated_max_drawdowns = np.zeros(num_simulations)
        simulated_drawdown_durations = np.zeros(num_simulations)
        simulated_95_dd_durations = np.zeros(num_simulations)
        simulated_sharpes = np.zeros(num_simulations)
        simulated_sortinos = np.zeros(num_simulations)
        simulated_max_consecutive_losses = np.zeros(num_simulations)
        simulated_cagrs = np.zeros(num_simulations)
        
        # Assuming about 365 trades represents a "year" of trading frequency for annualization metrics
        TRADES_PER_YEAR = 365
        
        for i in range(num_simulations):
            # Sample trades with replacement
            sampled_rois = np.random.choice(rois, size=trades_per_sim, replace=True)
            
            # Position sizing as a percentage of current capital
            # Cap the loss at 100% of the bet (ROI >= -1.0) to avoid negative capital multipliers from a single trade
            sampled_rois = np.maximum(sampled_rois, -1.0)
            capital_multipliers = 1 + (position_pct * sampled_rois)
            
            # Calculate equity curve
            equity_curve = initial_capital * np.cumprod(capital_multipliers)
            
            simulated_final_capitals[i] = equity_curve[-1]
            
            # Calculate drawdown
            running_max = np.maximum.accumulate(equity_curve)
            # Avoid divide by zero
            running_max_safe = np.where(running_max == 0, 1e-10, running_max)
            drawdowns = (running_max_safe - equity_curve) / running_max_safe
            simulated_max_drawdowns[i] = np.max(drawdowns)

            # Drawdown duration
            is_in_drawdown = equity_curve < running_max
            dd_changes = np.diff(np.concatenate(([0], is_in_drawdown.view(np.int8), [0])))
            dd_starts = np.where(dd_changes == 1)[0]
            dd_ends = np.where(dd_changes == -1)[0]
            if len(dd_starts) > 0:
                dd_durations = dd_ends - dd_starts
                simulated_drawdown_durations[i] = np.mean(dd_durations)
                simulated_95_dd_durations[i] = np.percentile(dd_durations, 95)
            else:
                simulated_drawdown_durations[i] = 0
                simulated_95_dd_durations[i] = 0

            # Sharpe and Sortino (Fixed Downside Deviation calculation)
            strat_returns = sampled_rois * position_pct
            avg_ret = np.mean(strat_returns)
            std_ret = np.std(strat_returns)
            
            if std_ret > 1e-6:
                simulated_sharpes[i] = (avg_ret / std_ret) * np.sqrt(TRADES_PER_YEAR)
            else:
                simulated_sharpes[i] = 0.0

            # Mathematically robust Downside Deviation formula: root of mean of squared negative returns
            downside_deviation = np.sqrt(np.mean(np.minimum(0, strat_returns)**2))
            
            if downside_deviation > 1e-6:
                simulated_sortinos[i] = (avg_ret / downside_deviation) * np.sqrt(TRADES_PER_YEAR)
            else:
                simulated_sortinos[i] = 0.0

            # Max consecutive losses
            is_loss = strat_returns < 0
            loss_changes = np.diff(np.concatenate(([0], is_loss.view(np.int8), [0])))
            loss_starts = np.where(loss_changes == 1)[0]
            loss_ends = np.where(loss_changes == -1)[0]
            if len(loss_starts) > 0:
                loss_streaks = loss_ends - loss_starts
                simulated_max_consecutive_losses[i] = np.max(loss_streaks)
            else:
                simulated_max_consecutive_losses[i] = 0

            # CAGR
            years = trades_per_sim / TRADES_PER_YEAR
            if years > 0 and equity_curve[-1] > 0:
                cagr = (equity_curve[-1] / initial_capital) ** (1 / years) - 1
                simulated_cagrs[i] = cagr
            else:
                # If bankrupt, -100% metric bounds it
                simulated_cagrs[i] = -1.0

        # Calculate statistics for the group
        mean_final = np.mean(simulated_final_capitals)
        median_final = np.median(simulated_final_capitals)
        p5_final = np.percentile(simulated_final_capitals, 5)
        p95_final = np.percentile(simulated_final_capitals, 95)
        std_final_capital = np.std(simulated_final_capitals)
        
        # Scaling percentage metrics by 100 for readability
        win_rate = np.mean(profits_per_share > 0) * 100 
        
        # Risk & Drawdown
        mean_dd = np.mean(simulated_max_drawdowns) * 100
        p95_dd = np.percentile(simulated_max_drawdowns, 95) * 100
        mean_dd_dur = np.mean(simulated_drawdown_durations)
        # Average of the 95th percentile worst duration across simulations
        p95_dd_dur = np.mean(simulated_95_dd_durations) 
        
        # Ratios
        mean_sharpe = np.mean(simulated_sharpes)
        mean_sortino = np.mean(simulated_sortinos)
        mean_cagr = np.mean(simulated_cagrs) * 100
        
        # Prevent RoMAD from overflowing if drawdown is incredibly small
        mean_dd_decimal = mean_dd / 100
        mean_romad = (mean_cagr / 100) / mean_dd_decimal if mean_dd_decimal > 1e-6 else 0.0
        
        mean_max_loss_streak = np.mean(simulated_max_consecutive_losses)
        p99_max_loss_streak = np.percentile(simulated_max_consecutive_losses, 99)

        # Basic definitions
        prob_ruin = np.mean(simulated_final_capitals <= (initial_capital * 0.05)) * 100 # consider ruin 95% loss of cap
        
        # Trade mechanics
        gross_profit = np.sum(profits_per_share[profits_per_share > 0])
        gross_loss = np.abs(np.sum(profits_per_share[profits_per_share < 0]))
        profit_factor = gross_profit / gross_loss if gross_loss > 1e-6 else float('inf')
        
        avg_win = np.mean(profits_per_share[profits_per_share > 0]) if np.any(profits_per_share > 0) else 0
        avg_loss = np.abs(np.mean(profits_per_share[profits_per_share < 0])) if np.any(profits_per_share < 0) else 0
        
        # Win rate needs to be decimal again for expectancy calculation
        expectancy = ((win_rate/100) * avg_win) - ((1 - (win_rate/100)) * avg_loss)

        results.append({
            'sec_before_close': sec_before_close,
            'price_threshold': price_threshold,
            'num_historical_trades': num_trades_in_group,
            'win_rate': round(win_rate, 2),
            'mean_final_capital': round(mean_final, 2),
            'median_final_capital': round(median_final, 2),
            'std_dev_final_capital': round(std_final_capital, 2),
            '5th_percentile_capital': round(p5_final, 2),
            '95th_percentile_capital': round(p95_final, 2),
            'mean_cagr': round(mean_cagr, 2),
            'prob_of_ruin': round(prob_ruin, 2),
            'mean_max_drawdown': round(mean_dd, 2),
            '95th_percentile_max_drawdown': round(p95_dd, 2),
            'mean_drawdown_duration': round(mean_dd_dur, 2),
            '95th_percentile_drawdown_duration': round(p95_dd_dur, 2),
            'mean_sharpe_ratio': round(mean_sharpe, 4),
            'mean_sortino_ratio': round(mean_sortino, 4),
            'mean_romad': round(mean_romad, 4),
            'mean_max_consecutive_losses': round(mean_max_loss_streak, 2),
            '99th_percentile_consecutive_losses': round(p99_max_loss_streak, 2),
            'mean_profit_factor': round(profit_factor, 4),
            'mean_expectancy': round(expectancy, 4)
        })

    results_df = pd.DataFrame(results)
    return results_df

def main():
    parser = argparse.ArgumentParser(description="Run Monte Carlo simulation on simulated trades.")
    parser.add_argument('--file', type=str, default='simulated_trades_dataset.csv', help='Path to the CSV dataset')
    parser.add_argument('--sims', type=int, default=10000, help='Number of simulations per group')
    parser.add_argument('--trades', type=int, default=100, help='Number of trades per simulation')
    parser.add_argument('--position-pct', type=float, default=0.10, help='Position size as a percentage of capital (e.g. 0.10 for 10%%)')
    args = parser.parse_args()

    print(f"Loading data from {args.file}...")
    try:
        df = pd.read_csv(args.file)
    except FileNotFoundError:
        print(f"Error: File '{args.file}' not found.")
        return

    print(f"Running Monte Carlo: {args.sims} simulations of {args.trades} trades per group...")
    print(f"Position size set to {args.position_pct * 100:.1f}% per trade.")
    summary_df = run_montecarlo(df, num_simulations=args.sims, trades_per_sim=args.trades, position_pct=args.position_pct)
    
    print("\n--- Monte Carlo Results ---")
    print(summary_df.to_string(index=False))
    
    # Dictionary to rename columns with units
    units_mapping = {
        'sec_before_close': 'sec_before_close_(s)',
        'price_threshold': 'price_threshold_(ratio)',
        'num_historical_trades': 'num_historical_trades_(count)',
        'win_rate': 'win_rate_(%)',
        'mean_final_capital': 'mean_final_capital_($)',
        'median_final_capital': 'median_final_capital_($)',
        'std_dev_final_capital': 'std_dev_final_capital_($)',
        '5th_percentile_capital': '5th_percentile_capital_($)',
        '95th_percentile_capital': '95th_percentile_capital_($)',
        'mean_cagr': 'mean_cagr_(%)',
        'prob_of_ruin': 'prob_of_ruin_(%)',
        'mean_max_drawdown': 'mean_max_drawdown_(%)',
        '95th_percentile_max_drawdown': '95th_percentile_max_drawdown_(%)',
        'mean_drawdown_duration': 'mean_drawdown_duration_(trades)',
        '95th_percentile_drawdown_duration': '95th_percentile_drawdown_duration_(trades)',
        'mean_sharpe_ratio': 'mean_sharpe_ratio_(ratio)',
        'mean_sortino_ratio': 'mean_sortino_ratio_(ratio)',
        'mean_romad': 'mean_romad_(ratio)',
        'mean_max_consecutive_losses': 'mean_max_consecutive_losses_(trades)',
        '99th_percentile_consecutive_losses': '99th_percentile_consecutive_losses_(trades)',
        'mean_profit_factor': 'mean_profit_factor_(ratio)',
        'mean_expectancy': 'mean_expectancy_($)'
    }
    
    # Save the output with new mapped column names
    summary_df_with_units = summary_df.rename(columns=units_mapping)
    output_file = 'montecarlo_summary_results.csv'
    summary_df_with_units.to_csv(output_file, index=False, sep=';', decimal=',')
    print(f"\nResults saved to {output_file} with appended column units.")

if __name__ == '__main__':
    main()