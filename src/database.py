import aiosqlite
import asyncio
from rich.console import Console
from datetime import datetime, timezone

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
    _conn = None
    _queue = None
    _running = False

    @classmethod
    async def init_db(cls):
        """Initializes the database explicitly with WAL mode and starts background writer."""
        if cls._conn is not None:
            return
            
        cls._queue = asyncio.Queue()
        cls._running = True
        
        try:
            cls._conn = await aiosqlite.connect(DB_NAME, timeout=15.0)
            # --- ENABLE WAL MODE ---
            await cls._conn.execute("PRAGMA journal_mode=WAL;")
            await cls._conn.execute("PRAGMA synchronous=NORMAL;")
            await cls._conn.execute("PRAGMA temp_store=MEMORY;")
            # -----------------------
            
            await cls._conn.executescript(INIT_SCRIPT)
            await cls._conn.commit()
            console.print("[green]✔ Database initialized successfully (Unified Schema, WAL Mode Enabled).[/green]")
            
            # Start writer loop
            asyncio.create_task(cls._writer_loop())
            
        except Exception as e:
            console.print(f"[red]✘ Database init failed: {e}[/red]")

    @classmethod
    async def _writer_loop(cls):
        batch = []
        while cls._running or not cls._queue.empty():
            try:
                item = await asyncio.wait_for(cls._queue.get(), timeout=1.0)
                batch.append(item)
                cls._queue.task_done()
            except asyncio.TimeoutError:
                pass
            
            if batch:
                try:
                    for query, params in batch:
                        try:
                            await cls._conn.execute(query, params)
                        except Exception as inner_e:
                            console.print(f"[red]Failed query: {query} | Error: {inner_e}[/red]")
                    await cls._conn.commit()
                except Exception as e:
                    console.print(f"[red]Database insertion error: {e}[/red]")
                finally:
                    batch.clear()

    @classmethod
    async def _execute(cls, query, params=()):
        if cls._queue:
            await cls._queue.put((query, params))

    @staticmethod
    async def log_whale_activity(wallet, condition_id, token_id_yes, token_id_no, title, outcome, side, size, price, timestamp):
        """Inserts a whale trade into the unified trades table. (Now queued)"""
        # 1. Ensure Market Exists
        await Database._execute("""
            INSERT OR IGNORE INTO markets (market_id, title, last_price, last_updated)
            VALUES (?, ?, ?, ?)
        """, (condition_id, title, price, timestamp))
        
        # Update market assets if provided
        if token_id_yes:
            await Database._execute("INSERT OR IGNORE INTO market_assets (asset_id, market_id, outcome) VALUES (?, ?, ?)", (token_id_yes, condition_id, "Yes"))
        if token_id_no:
            await Database._execute("INSERT OR IGNORE INTO market_assets (asset_id, market_id, outcome) VALUES (?, ?, ?)", (token_id_no, condition_id, "No"))

        # 2. Ensure Wallet Exists
        await Database._execute("""
            INSERT OR IGNORE INTO wallets (address, last_updated)
            VALUES (?, ?)
        """, (wallet, timestamp))

        asset_id = None
        if outcome.lower() == 'yes' and token_id_yes:
            asset_id = token_id_yes
        elif outcome.lower() == 'no' and token_id_no:
            asset_id = token_id_no

        # For aggregation in whale activity, it gets tricky with queues.
        # But we can just do reads from an internal fast connection manually if needed, or open a short connection for reads.
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            time_window = 60
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
            trade_id, old_price, old_size = existing_trade
            new_size = old_size + size
            new_price = ((old_price * old_size) + (price * size)) / new_size if new_size > 0 else price
            
            await Database._execute("""
                UPDATE trades 
                SET size = ?, price = ?, timestamp = ? 
                WHERE trade_id = ?
            """, (new_size, new_price, timestamp, trade_id))
            console.print(f"[dim]DB: Aggregated trade for {wallet[:6]} (New Size: ${new_size:.2f})[/dim]")
        else:
            await Database._execute("""
                INSERT INTO trades (trade_source, market_id, asset_id, wallet_address, side, price, size, timestamp)
                VALUES ('WHALE', ?, ?, ?, ?, ?, ?, ?)
            """, (condition_id, asset_id, wallet, side, price, size, timestamp))
            console.print(f"[dim]DB: Logged new trade for {wallet[:6]}[/dim]")

    @staticmethod
    async def log_bot_trade(condition_id, outcome, side, entry_price, size_usd, status='OPEN'):
        timestamp = int(datetime.now(timezone.utc).timestamp())
        
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT asset_id FROM market_assets WHERE market_id = ? AND LOWER(outcome) = LOWER(?)", (condition_id, outcome))
            row = await cursor.fetchone()
            asset_id = row[0] if row else None
            
        await Database._execute("""
            INSERT INTO trades (trade_source, market_id, asset_id, side, price, size, status, timestamp)
            VALUES ('BOT', ?, ?, ?, ?, ?, ?, ?)
        """, (condition_id, asset_id, side, entry_price, size_usd, status, timestamp))
        console.print(f"[dim]DB: Logged BOT trade {status} for {outcome} ({side})[/dim]")

    @staticmethod
    async def update_bot_pnl(trade_id, pnl_usd):
        pass

    @staticmethod
    async def find_open_trade(condition_id, outcome):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
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
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT price, size, side FROM trades WHERE trade_id = ?", (trade_id,))
            row = await cursor.fetchone()
            
        if row:
            entry_price, size, side = row
            shares = size / entry_price if entry_price > 0 else 0
            pnl = (exit_price - entry_price) * shares
            
            await Database._execute("""
                UPDATE trades 
                SET status = 'CLOSED', realized_pnl = ? 
                WHERE trade_id = ?
            """, (pnl, trade_id))
            console.print(f"[green]DB: Closed trade {trade_id} | PnL: ${pnl:.2f}[/green]")

    @staticmethod
    async def log_ws_market(asset_id, parent_market_id, question, outcome, created_at):
        await Database._execute(
            "INSERT OR IGNORE INTO markets (market_id, title, created_at) VALUES (?, ?, ?)",
            (parent_market_id, question, created_at)
        )
        await Database._execute(
            "INSERT OR IGNORE INTO market_assets (asset_id, market_id, outcome) VALUES (?, ?, ?)",
            (asset_id, parent_market_id, outcome)
        )

    @staticmethod
    async def log_best_bid_ask(timestamp, market_id, best_bid, best_ask, spread):
        await Database._execute(
            "INSERT OR REPLACE INTO best_bid_ask (market_id, timestamp, best_bid, best_ask, spread) VALUES (?, ?, ?, ?, ?)",
            (market_id, timestamp, best_bid, best_ask, spread)
        )

    @staticmethod
    async def log_trade(timestamp, market_id, price, side, size):
        # Fire off a separate query directly to get the parent_id to avoid lock conflicts
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT market_id FROM market_assets WHERE asset_id = ?", (market_id,))
            row = await cursor.fetchone()
            parent_id = row[0] if row else market_id

        await Database._execute(
            "INSERT INTO trades (trade_source, market_id, asset_id, price, side, size, timestamp) VALUES ('PUBLIC_WS', ?, ?, ?, ?, ?, ?)",
            (parent_id, market_id, price, side, size, timestamp)
        )

    @staticmethod
    async def log_price_change(timestamp, market_id, price, size, side):
        await Database._execute(
            "INSERT OR REPLACE INTO price_changes (market_id, timestamp, price, size, side) VALUES (?, ?, ?, ?, ?)",
            (market_id, timestamp, price, size, side)
        )

    @staticmethod
    async def log_tick_size_change(timestamp, market_id, old_tick_size, new_tick_size):
        await Database._execute(
            "INSERT OR REPLACE INTO tick_size_changes (market_id, timestamp, old_tick_size, new_tick_size) VALUES (?, ?, ?, ?)",
            (market_id, timestamp, old_tick_size, new_tick_size)
        )

    @staticmethod
    async def log_paper_trade(timestamp, asset_id, buy_price, seconds_left):
        async with aiosqlite.connect(DB_NAME, timeout=15.0) as db:
            cursor = await db.execute("SELECT market_id FROM market_assets WHERE asset_id = ?", (asset_id,))
            row = await cursor.fetchone()
            parent_id = row[0] if row else asset_id
            
        await Database._execute(
            "INSERT INTO trades (trade_source, market_id, asset_id, price, seconds_left, timestamp) VALUES ('PAPER', ?, ?, ?, ?, ?)",
            (parent_id, asset_id, buy_price, seconds_left, timestamp)
        )

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
        await Database._execute(
            "UPDATE trades SET realized_pnl = ?, resolved_price = ? WHERE trade_id = ?",
            (pnl, resolved_price, trade_id)
        )
