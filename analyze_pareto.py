import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Load the data
df = pd.read_csv('paper_trades.csv')

# Filter for prices between 0.75 and 0.99
df_filtered = df[(df['price'] >= 0.75) & (df['price'] <= 0.99)].copy()

# Determine wins and losses
df_filtered['is_win'] = df_filtered['realized_pnl'] > 0
df_filtered['is_loss'] = df_filtered['realized_pnl'] < 0

# Create price bins (every 0.05)
bins = np.arange(0.75, 1.01, 0.05)
labels = [f"{b:.2f}-{b+0.04:.2f}" for b in bins[:-1]]
df_filtered['price_bin'] = pd.cut(df_filtered['price'], bins=bins, labels=labels, right=False)

# Aggregate data
summary = df_filtered.groupby('price_bin').agg(
    total_trades=('trade_id', 'count'),
    wins=('is_win', 'sum'),
    losses=('is_loss', 'sum'),
    pnl=('realized_pnl', 'sum')
).fillna(0)

# Sort by total trades to see Pareto distribution
summary = summary.sort_values(by='total_trades', ascending=False)

# Plotting
fig, ax1 = plt.subplots(figsize=(12, 6))

summary[['wins', 'losses']].plot(kind='bar', stacked=True, color=['#2ca02c', '#d62728'], ax=ax1)

ax1.set_ylabel('Number of Trades')
ax1.set_xlabel('Price Range')
ax1.set_title('Trades by Price Range (Sorted by Volume)')

# Add cumulative percentage line for total trades (Pareto line)
ax2 = ax1.twinx()
cumulative_pct = (summary['total_trades'].cumsum() / summary['total_trades'].sum()) * 100
ax2.plot(range(len(summary)), cumulative_pct, color='blue', marker='o', ms=5, linewidth=2)
ax2.set_ylabel('Cumulative % of Trades')
ax2.set_ylim(0, 105)

plt.axhline(80, color='gray', linestyle='--', alpha=0.5)

plt.tight_layout()
plt.savefig('pareto_analysis.png')
print("Saved pareto analysis chart to pareto_analysis.png")
print("\nData Summary:")
print(summary)
