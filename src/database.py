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
    token_id TEXT, -- The "asset" ID from API
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
                await db.commit()
            console.print("[green]✔ Database initialized successfully (WAL Mode Enabled).[/green]")
        except Exception as e:
            console.print(f"[red]✘ Database init failed: {e}[/red]")

    @staticmethod
    async def log_whale_activity(wallet, condition_id, title, outcome, side, size, price, timestamp):
        """Inserts a whale trade into the database."""
        
        async with aiosqlite.connect(DB_NAME) as db:
            # 1. Ensure Market Exists
            await db.execute("""
                INSERT OR IGNORE INTO markets (condition_id, title, last_price, last_updated)
                VALUES (?, ?, ?, ?)
            """, (condition_id, title, price, timestamp))

            # 2. Ensure Wallet Exists
            await db.execute("""
                INSERT OR IGNORE INTO wallets (address, last_updated)
                VALUES (?, ?)
            """, (wallet, timestamp))

            # 3. Log Trade
            await db.execute("""
                INSERT INTO wallet_trades (wallet_address, condition_id, outcome, side, entry_price, size_usd, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (wallet, condition_id, outcome, side, price, size, timestamp))
            
            await db.commit()
            console.print(f"[dim]DB: Logged trade for {wallet[:6]}[/dim]")
