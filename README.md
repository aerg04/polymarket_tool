# Polymarket Bot

## Setup & Installation

1. **Create Virtual Environment & Install Dependencies**
   ```bash
   python -m venv venv
   source venv/bin/activate
   
   # Install standard trading dependencies
   pip install -r requirements.txt
   
   # Install development and analysis dependencies (if you have a separate dev file)
   # pip install -r requirements-dev.txt
   ```

2. **Data Directory**
   Create a `data` folder at the root of the project to store the database and analysis results. This is required to keep things organized locally and is essential for persistence when running in Docker.
   ```bash
   mkdir data
   ```

## Database Management

To safely backup your SQLite database before running heavy queries or migrations (even while the bot is running), use the SQLite backup command:

```bash
sqlite3 data/polymarket_bot.db ".backup data/polymarket_bot_backup.db"
```
*(Note: Adjust the path if your `.db` file is stored in the core directory instead of `/data/`)*

## Analysis & Paper Trades

Once the bot operates and accumulates data, you can analyze its paper trading performance using the included scripts:

1. **Extract Paper Trades**
   Extracts and formats the raw paper trading data from the SQLite database to CSV or for terminal output.
   ```bash
   python extract_paper_trades.py
   ```

2. **Monte Carlo Simulations**
   Runs Monte Carlo statistical simulations on the extracted potential trades to model risk, variance, and expected profit over time.
   ```bash
   python montecarlo_simulated_trades.py
   ```

## Docker Operations

**Build the image:**
```bash
docker build -t polymarket-bot .
```