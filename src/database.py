import aiosqlite
import asyncio
from rich.console import Console
from datetime import datetime
import hashlib

DB_NAME = "polymarket_bot.db"
console = Console()

INIT_SCRIPT = """
-- 1. Wallets (The Whales)
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    alias TEXT,
    risk_score REAL DEFAULT 0.5,
    total_profit REAL DEFAULT 0,
    active INTEGER DEFAULT 1,
    last_updated INTEGER
);

-- 2. Markets (Context for the trades)
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY, 
    token_id_yes TEXT, -- Token ID for 'Yes' outcome
    token_id_no TEXT, -- Token ID for 'No' outcome
    title TEXT,
    last_price REAL,
    volume_usd REAL,
    is_resolved INTEGER DEFAULT 0,
    last_updated INTEGER
);

-- 3. Wallet Trades (Whale Activity)
CREATE TABLE IF NOT EXISTS wallet_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT,
    condition_id TEXT,
    outcome TEXT,
    side TEXT,
    entry_price REAL,
    size_usd REAL,
    timestamp INTEGER,
    FOREIGN KEY(wallet_address) REFERENCES wallets(address),
    FOREIGN KEY(condition_id) REFERENCES markets(condition_id)
);

-- 4. Bot Trades (Your Copy Trades)
CREATE TABLE IF NOT EXISTS bot_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT,
    outcome TEXT,
    entry_price REAL,
    size_usd REAL,
    status TEXT DEFAULT 'OPEN', -- OPEN, CLOSED
    realized_pnl REAL DEFAULT 0,
    timestamp INTEGER,
    FOREIGN KEY(condition_id) REFERENCES markets(condition_id)
);
"""

class Database:
    @staticmethod
    async def init_db():
        """Initializes the database explicitly with WAL mode."""
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                # --- ENABLE WAL MODE ---
                await db.execute("PRAGMA journal_mode=WAL;")
                # -----------------------
                
                await db.executescript(INIT_SCRIPT)
                
                # Migration: Add token_id_yes and token_id_no if they don't exist
                try:
                    await db.execute("ALTER TABLE markets ADD COLUMN token_id_yes TEXT")
                    console.print("[yellow]Migrated DB: Added token_id_yes column[/yellow]")
                except Exception:
                    pass # Column likely exists
                
                try:
                    await db.execute("ALTER TABLE markets ADD COLUMN token_id_no TEXT")
                    console.print("[yellow]Migrated DB: Added token_id_no column[/yellow]")
                except Exception:
                    pass # Column likely exists

                await db.commit()
            console.print("[green]✔ Database initialized successfully (WAL Mode Enabled).[/green]")
        except Exception as e:
            console.print(f"[red]✘ Database init failed: {e}[/red]")

    @staticmethod
    async def log_whale_activity(wallet, condition_id, token_id_yes, token_id_no, title, outcome, side, size, price, timestamp):
        """Inserts a whale trade into the database. Aggregates partial fills within a short window."""
        
        async with aiosqlite.connect(DB_NAME) as db:
            # 1. Ensure Market Exists (and update token_ids if provided)
            await db.execute("""
                INSERT OR IGNORE INTO markets (condition_id, token_id_yes, token_id_no, title, last_price, last_updated)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (condition_id, token_id_yes, token_id_no, title, price, timestamp))
            
            # If entry exists but token_ids are updated
            if token_id_yes:
                await db.execute("UPDATE markets SET token_id_yes = ? WHERE condition_id = ?", (token_id_yes, condition_id))
            if token_id_no:
                await db.execute("UPDATE markets SET token_id_no = ? WHERE condition_id = ?", (token_id_no, condition_id))

            # 2. Ensure Wallet Exists
            await db.execute("""
                INSERT OR IGNORE INTO wallets (address, last_updated)
                VALUES (?, ?)
            """, (wallet, timestamp))

            # 3. Log Trade (with aggregation for partial fills)
            # Check for existing trade from same wallet, market, side, outcome within last 60 seconds
            time_window = 60 
            cursor = await db.execute("""
                SELECT id, entry_price, size_usd 
                FROM wallet_trades 
                WHERE wallet_address = ? 
                  AND condition_id = ? 
                  AND side = ? 
                  AND outcome = ? 
                  AND timestamp >= ?
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (wallet, condition_id, side, outcome, timestamp - time_window))
            
            existing_trade = await cursor.fetchone()
            
            if existing_trade:
                # Update existing trade
                trade_id, old_price, old_size = existing_trade
                new_size = old_size + size
                if new_size > 0:
                    # Weighted average price
                    new_price = ((old_price * old_size) + (price * size)) / new_size
                else:
                    new_price = price # Should not happen unless negative sizes
                
                await db.execute("""
                    UPDATE wallet_trades 
                    SET size_usd = ?, entry_price = ?, timestamp = ? 
                    WHERE id = ?
                """, (new_size, new_price, timestamp, trade_id))
                console.print(f"[dim]DB: Aggregated trade for {wallet[:6]} (New Size: ${new_size:.2f})[/dim]")
            else:
                # Insert new trade
                await db.execute("""
                    INSERT INTO wallet_trades (wallet_address, condition_id, outcome, side, entry_price, size_usd, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (wallet, condition_id, outcome, side, price, size, timestamp))
                console.print(f"[dim]DB: Logged new trade for {wallet[:6]}[/dim]")
            
            await db.commit()
