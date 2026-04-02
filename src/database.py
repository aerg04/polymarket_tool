import aiosqlite
import asyncio
from rich.console import Console
from datetime import datetime
import hashlib

DB_NAME = "polymarket_bot.db"
console = Console()

INIT_SCRIPT = """
-- 1. Wallets (Entities trading)
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    alias TEXT,
    risk_score REAL DEFAULT 0.5,
    total_profit REAL DEFAULT 0,
    active INTEGER DEFAULT 1,
    last_updated INTEGER
);

-- 2. Markets (Core market details, combining 'markets' and 'ws_markets' parent data)
CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY, -- Replaces condition_id / parent_market_id
    title TEXT,                 -- Replaces question
    is_resolved INTEGER DEFAULT 0,
    last_price REAL,
    volume_usd REAL,
    created_at REAL,
    last_updated INTEGER
);

-- 3. Market Outcomes / Assets (Resolves hardcoded YES/NO tokens and WS assets)
CREATE TABLE IF NOT EXISTS market_assets (
    asset_id TEXT PRIMARY KEY, -- Specific token/asset ID for an outcome
    market_id TEXT,
    outcome TEXT,              -- 'Yes', 'No', or other multi-choice answers
    FOREIGN KEY(market_id) REFERENCES markets(market_id)
);

-- 4. Unified Trades Table (Combines wallet_trades, bot_trades, trades, and paper_trades)
CREATE TABLE IF NOT EXISTS trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_source TEXT NOT NULL, -- 'WHALE', 'BOT', 'PAPER', 'PUBLIC_WS'
    market_id TEXT NOT NULL,
    asset_id TEXT,              -- References market_assets. Nullable if general market trade
    wallet_address TEXT,        -- Nullable (only applies to WHALE or BOT if tracked)
    side TEXT,                  -- 'BUY', 'SELL'
    price REAL,                 -- Unified entry_price / buy_price
    size REAL,                  -- Unified size_usd / size
    timestamp REAL NOT NULL,
    
    -- Bot/Paper specific columns (Nullable for standard/public trades)
    status TEXT DEFAULT 'CLOSED', -- OPEN, CLOSED (for bots)
    realized_pnl REAL DEFAULT 0,
    seconds_left INTEGER,         -- For paper trades
    resolved_price REAL,          -- For paper trades
    
    FOREIGN KEY(market_id) REFERENCES markets(market_id),
    FOREIGN KEY(asset_id) REFERENCES market_assets(asset_id),
    FOREIGN KEY(wallet_address) REFERENCES wallets(address)
);

-- 5. Time-Series / Orderbook Data (Added Composite PKs for 1NF/2NF compliance)
CREATE TABLE IF NOT EXISTS best_bid_ask (
    market_id TEXT,
    timestamp REAL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    PRIMARY KEY (market_id, timestamp),
    FOREIGN KEY(market_id) REFERENCES markets(market_id)
);

CREATE TABLE IF NOT EXISTS price_changes (
    market_id TEXT,
    timestamp REAL,
    price REAL,
    size REAL,
    side TEXT,
    PRIMARY KEY (market_id, timestamp),
    FOREIGN KEY(market_id) REFERENCES markets(market_id)
);

CREATE TABLE IF NOT EXISTS tick_size_changes (
    market_id TEXT,
    timestamp REAL,
    old_tick_size REAL,
    new_tick_size REAL,
    PRIMARY KEY (market_id, timestamp),
    FOREIGN KEY(market_id) REFERENCES markets(market_id)
);
"""

class Database:
    @staticmethod
    async def init_db():
        """Initializes the database explicitly with WAL mode."""
        try:
            async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
                # --- ENABLE WAL MODE ---
                await db.execute("PRAGMA journal_mode=WAL;")
                # -----------------------
                
                await db.executescript(INIT_SCRIPT)
                await db.commit()
            console.print("[green]✔ Database initialized successfully (Unified Schema, WAL Mode Enabled).[/green]")
        except Exception as e:
            console.print(f"[red]✘ Database init failed: {e}[/red]")

    @staticmethod
    async def log_whale_activity(wallet, condition_id, token_id_yes, token_id_no, title, outcome, side, size, price, timestamp):
        """Inserts a whale trade into the unified trades table. Aggregates partial fills within a short window."""
        
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            # 1. Ensure Market Exists
            await db.execute("""
                INSERT OR IGNORE INTO markets (market_id, title, last_price, last_updated)
                VALUES (?, ?, ?, ?)
            """, (condition_id, title, price, timestamp))
            
            # Update market assets if provided
            if token_id_yes:
                await db.execute("INSERT OR IGNORE INTO market_assets (asset_id, market_id, outcome) VALUES (?, ?, ?)", (token_id_yes, condition_id, "Yes"))
            if token_id_no:
                await db.execute("INSERT OR IGNORE INTO market_assets (asset_id, market_id, outcome) VALUES (?, ?, ?)", (token_id_no, condition_id, "No"))

            # 2. Ensure Wallet Exists
            await db.execute("""
                INSERT OR IGNORE INTO wallets (address, last_updated)
                VALUES (?, ?)
            """, (wallet, timestamp))

            # Determine appropriate asset_id based on outcome
            asset_id = None
            if outcome.lower() == 'yes' and token_id_yes:
                asset_id = token_id_yes
            elif outcome.lower() == 'no' and token_id_no:
                asset_id = token_id_no

            # 3. Log Trade (with aggregation for partial fills)
            time_window = 60 
            
            # Check for existing WHALE trade
            cursor = await db.execute("""
                SELECT trade_id, price, size 
                FROM trades 
                WHERE trade_source = 'WHALE'
                  AND wallet_address = ? 
                  AND market_id = ? 
                  AND side = ? 
                  AND (asset_id = ? OR asset_id IS NULL)
                  AND timestamp >= ?
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (wallet, condition_id, side, asset_id, timestamp - time_window))
            
            existing_trade = await cursor.fetchone()
            
            if existing_trade:
                # Update existing trade
                trade_id, old_price, old_size = existing_trade
                new_size = old_size + size
                if new_size > 0:
                    new_price = ((old_price * old_size) + (price * size)) / new_size
                else:
                    new_price = price
                
                await db.execute("""
                    UPDATE trades 
                    SET size = ?, price = ?, timestamp = ? 
                    WHERE trade_id = ?
                """, (new_size, new_price, timestamp, trade_id))
                console.print(f"[dim]DB: Aggregated trade for {wallet[:6]} (New Size: ${new_size:.2f})[/dim]")
            else:
                # Insert new trade
                await db.execute("""
                    INSERT INTO trades (trade_source, market_id, asset_id, wallet_address, side, price, size, timestamp)
                    VALUES ('WHALE', ?, ?, ?, ?, ?, ?, ?)
                """, (condition_id, asset_id, wallet, side, price, size, timestamp))
                console.print(f"[dim]DB: Logged new trade for {wallet[:6]}[/dim]")
            
            await db.commit()

    @staticmethod
    async def log_bot_trade(condition_id, outcome, side, entry_price, size_usd, status='OPEN'):
        """Logs a trade made by the bot (Real or Simulated) using the unified schema."""
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            timestamp = int(datetime.utcnow().timestamp())
            
            # Attempt to find the asset_id based on outcome
            cursor = await db.execute("SELECT asset_id FROM market_assets WHERE market_id = ? AND LOWER(outcome) = LOWER(?)", (condition_id, outcome))
            row = await cursor.fetchone()
            asset_id = row[0] if row else None
            
            await db.execute("""
                INSERT INTO trades (trade_source, market_id, asset_id, side, price, size, status, timestamp)
                VALUES ('BOT', ?, ?, ?, ?, ?, ?, ?)
            """, (condition_id, asset_id, side, entry_price, size_usd, status, timestamp))
            await db.commit()
            console.print(f"[dim]DB: Logged BOT trade {status} for {outcome} ({side})[/dim]")

    @staticmethod
    async def update_bot_pnl(trade_id, pnl_usd):
        pass

    @staticmethod
    async def find_open_trade(condition_id, outcome):
        """Finds the most recent OPEN BOT trade for a given market/condition and outcome."""
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            # Join with market_assets if we need to match precisely by outcome
            cursor = await db.execute("""
                SELECT t.trade_id, t.price, t.size, t.status
                FROM trades t
                LEFT JOIN market_assets m ON t.asset_id = m.asset_id
                WHERE t.trade_source = 'BOT'
                  AND t.market_id = ? 
                  AND (m.outcome IS NULL OR LOWER(m.outcome) = LOWER(?))
                  AND t.status IN ('OPEN', 'SIMULATED_OPEN')
                ORDER BY t.timestamp DESC
                LIMIT 1
            """, (condition_id, outcome))
            return await cursor.fetchone()

    @staticmethod
    async def close_bot_trade(trade_id, exit_price):
        """Closes a BOT trade and calculates final PnL."""
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT price, size, side FROM trades WHERE trade_id = ?", (trade_id,))
            row = await cursor.fetchone()
            if row:
                entry_price, size, side = row
                
                shares = size / entry_price if entry_price > 0 else 0
                pnl = (exit_price - entry_price) * shares
                
                await db.execute("""
                    UPDATE trades 
                    SET status = 'CLOSED', realized_pnl = ? 
                    WHERE trade_id = ?
                """, (pnl, trade_id))
                await db.commit()
                console.print(f"[green]DB: Closed trade {trade_id} | PnL: ${pnl:.2f}[/green]")

    @staticmethod
    async def log_ws_market(asset_id, parent_market_id, question, outcome, created_at):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute(
                "INSERT OR IGNORE INTO markets (market_id, title, created_at) VALUES (?, ?, ?)",
                (parent_market_id, question, created_at)
            )
            await db.execute(
                "INSERT OR IGNORE INTO market_assets (asset_id, market_id, outcome) VALUES (?, ?, ?)",
                (asset_id, parent_market_id, outcome)
            )
            await db.commit()

    @staticmethod
    async def log_best_bid_ask(timestamp, market_id, best_bid, best_ask, spread):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute(
                "INSERT OR REPLACE INTO best_bid_ask (market_id, timestamp, best_bid, best_ask, spread) VALUES (?, ?, ?, ?, ?)",
                (market_id, timestamp, best_bid, best_ask, spread)
            )
            await db.commit()

    @staticmethod
    async def log_trade(timestamp, market_id, price, side, size):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            # For WS public trades, market_id typically represents the token/asset.
            # We attempt to find the parent market_id.
            cursor = await db.execute("SELECT market_id FROM market_assets WHERE asset_id = ?", (market_id,))
            row = await cursor.fetchone()
            parent_id = row[0] if row else market_id

            await db.execute(
                "INSERT INTO trades (trade_source, market_id, asset_id, price, side, size, timestamp) VALUES ('PUBLIC_WS', ?, ?, ?, ?, ?, ?)",
                (parent_id, market_id, price, side, size, timestamp)
            )
            await db.commit()

    @staticmethod
    async def log_price_change(timestamp, market_id, price, size, side):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            # Often asset_id is passed here instead of market_id
            await db.execute(
                "INSERT OR REPLACE INTO price_changes (market_id, timestamp, price, size, side) VALUES (?, ?, ?, ?, ?)",
                (market_id, timestamp, price, size, side)
            )
            await db.commit()

    @staticmethod
    async def log_tick_size_change(timestamp, market_id, old_tick_size, new_tick_size):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute(
                "INSERT OR REPLACE INTO tick_size_changes (market_id, timestamp, old_tick_size, new_tick_size) VALUES (?, ?, ?, ?)",
                (market_id, timestamp, old_tick_size, new_tick_size)
            )
            await db.commit()

    @staticmethod
    async def log_paper_trade(timestamp, asset_id, buy_price, seconds_left):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT market_id FROM market_assets WHERE asset_id = ?", (asset_id,))
            row = await cursor.fetchone()
            parent_id = row[0] if row else asset_id
            
            await db.execute(
                "INSERT INTO trades (trade_source, market_id, asset_id, price, seconds_left, timestamp) VALUES ('PAPER', ?, ?, ?, ?, ?)",
                (parent_id, asset_id, buy_price, seconds_left, timestamp)
            )
            await db.commit()

    @staticmethod
    async def get_unresolved_paper_trades():
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT trade_id, asset_id, price FROM trades WHERE trade_source = 'PAPER' AND realized_pnl IS NULL")
            return await cursor.fetchall()
            
    @staticmethod
    async def get_parent_market_id(asset_id):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT market_id FROM market_assets WHERE asset_id = ?", (asset_id,))
            row = await cursor.fetchone()
            if row:
                return row[0]
            return None

    @staticmethod
    async def update_paper_trade_pnl(trade_id, pnl, resolved_price):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            await db.execute(
                "UPDATE trades SET realized_pnl = ?, resolved_price = ? WHERE trade_id = ?",
                (pnl, resolved_price, trade_id)
            )
            await db.commit()

