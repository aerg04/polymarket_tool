import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
import statsmodels.api as sm

# 1. Load Offline Data
df_trades = pd.read_csv('paper_trades3.csv')
btc = pd.read_csv('btc_klines.csv')

# Format timestamps
df_trades['datetime'] = pd.to_datetime(df_trades['timestamp'], unit='s').astype('datetime64[ns]')
btc['open_time'] = pd.to_datetime(btc['open_time']).astype('datetime64[ns]')

# Define Win vs Stop Loss (SL) based on realized_pnl
df_trades['result'] = np.where(df_trades['realized_pnl'] > 0, 'Win', 'Loss (SL)')
# For the Logistic Regression later: 1 if SL hit, 0 if Win
df_trades['sl_hit'] = np.where(df_trades['realized_pnl'] <= 0, 1, 0)

# 2. Calculate Market Indicators (Choppiness, ATR, RSI)
n = 14

# True Range is used for both CHOP and ATR
btc['tr'] = np.maximum(btc['high'] - btc['low'], 
                       np.maximum(abs(btc['high'] - btc['close'].shift()), 
                                  abs(btc['low'] - btc['close'].shift())))

# BTC Choppiness Index
btc['atr_sum'] = btc['tr'].rolling(n).sum()
btc['max_high'] = btc['high'].rolling(n).max()
btc['min_low'] = btc['low'].rolling(n).min()
btc['chop'] = 100 * np.log10(btc['atr_sum'] / (btc['max_high'] - btc['min_low'])) / np.log10(n)

# BTC Average True Range (ATR)
btc['atr'] = btc['tr'].rolling(n).mean()

# BTC Relative Strength Index (RSI)
delta = btc['close'].diff()
gain = delta.where(delta > 0, 0)
loss = -delta.where(delta < 0, 0)
avg_gain = gain.ewm(alpha=1/n, adjust=False).mean()
avg_loss = loss.ewm(alpha=1/n, adjust=False).mean()
rs = avg_gain / avg_loss
btc['rsi'] = 100 - (100 / (1 + rs))

# 3. Merge Market Conditions to the Exact Time of the Trade
df_trades = df_trades.sort_values('datetime')
btc = btc.sort_values('open_time')

# pd.merge_asof matches the trade to the closest prior 5m candle
df_merged = pd.merge_asof(df_trades, btc[['open_time', 'chop', 'atr', 'rsi']], left_on='datetime', right_on='open_time', direction='backward')

# Drop NA rows where indicators were still calculating (the first 14 periods)
df_merged = df_merged.dropna(subset=['chop', 'atr', 'rsi'])

# 4. Plotting
fig, axes = plt.subplots(3, 1, figsize=(12, 15))
palette = {'Win': '#2ca02c', 'Loss (SL)': '#d62728'}

# Plot CHOP
sns.scatterplot(data=df_merged, x='datetime', y='chop', hue='result', ax=axes[0], s=80, palette=palette, alpha=0.7)
axes[0].set_title('Polymarket Trades vs BTC Choppiness Index (5m)')
axes[0].set_ylabel('Choppiness Index')
axes[0].axhline(61.8, ls='--', color='gray', alpha=0.5, label='High Chop (>61.8)')
axes[0].axhline(38.2, ls='--', color='gray', alpha=0.5, label='Low Chop (<38.2)')
axes[0].legend()

# Plot ATR
sns.scatterplot(data=df_merged, x='datetime', y='atr', hue='result', ax=axes[1], s=80, palette=palette, alpha=0.7)
axes[1].set_title('Polymarket Trades vs BTC Average True Range (5m)')
axes[1].set_ylabel('ATR')
axes[1].legend()

# Plot RSI
sns.scatterplot(data=df_merged, x='datetime', y='rsi', hue='result', ax=axes[2], s=80, palette=palette, alpha=0.7)
axes[2].set_title('Polymarket Trades vs BTC RSI (5m)')
axes[2].set_ylabel('RSI')
axes[2].axhline(70, ls='--', color='gray', alpha=0.5, label='Overbought (>70)')
axes[2].axhline(30, ls='--', color='gray', alpha=0.5, label='Oversold (<30)')
axes[2].legend()

plt.tight_layout()
plt.savefig('trade_analysis_chart.png')
print("Charts saved to trade_analysis_chart.png")

# 5. Statistical Analysis
print("\n=== STATISTICAL ANALYSIS ===")

# Mann-Whitney U Test (Non-parametric test for comparing two groups)
wins_chop = df_merged[df_merged['result'] == 'Win']['chop']
losses_chop = df_merged[df_merged['result'] == 'Loss (SL)']['chop']
stat, p_chop = stats.mannwhitneyu(wins_chop, losses_chop, alternative='two-sided')
print(f"Mann-Whitney U Test (Choppiness): p-value = {p_chop:.4f}")

wins_atr = df_merged[df_merged['result'] == 'Win']['atr']
losses_atr = df_merged[df_merged['result'] == 'Loss (SL)']['atr']
stat, p_atr = stats.mannwhitneyu(wins_atr, losses_atr, alternative='two-sided')
print(f"Mann-Whitney U Test (ATR): p-value = {p_atr:.4f}")

wins_rsi = df_merged[df_merged['result'] == 'Win']['rsi']
losses_rsi = df_merged[df_merged['result'] == 'Loss (SL)']['rsi']
stat, p_rsi = stats.mannwhitneyu(wins_rsi, losses_rsi, alternative='two-sided')
print(f"Mann-Whitney U Test (RSI): p-value = {p_rsi:.4f}")

print("\n--- Logistic Regression (Predicting Stop Loss Hits) ---")
# Independent variables (X) and Dependent variable (y)
X = df_merged[['chop', 'atr', 'rsi']]
X = sm.add_constant(X) # Adds an intercept
y = df_merged['sl_hit']

try:
    log_reg = sm.Logit(y, X).fit(disp=0)
    print(log_reg.summary())
except Exception as e:
    print(f"Could not run Logistic Regression (Likely not enough SL data yet): {e}")

df_merged.to_csv('fully_processed_trades1.csv', index=False)
print("\nFinal merged dataset saved to fully_processed_trades1.csv")