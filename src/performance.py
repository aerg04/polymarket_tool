import asyncio
from src.database import Database, DB_NAME
from src.market_api import MarketAPI
from src.config import console
import aiosqlite

class PerformanceTracker:
    def __init__(self):
        self.interval = 60 # Check every minute

    async def run(self):
        console.print("[bold green]🚀 Performance Tracker Started (Background)[/bold green]")
        while True:
            try:
                await self.update_pnl()
            except Exception as e:
                console.print(f"[red]Performance Tracker Error: {e}[/red]")
            
            await asyncio.sleep(self.interval)

    async def update_pnl(self):
        # Fetch inclusive open trades (Real and Simulated)
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            # Join with markets table to get the title
            cursor = await db.execute("""
                SELECT t.id, t.condition_id, t.outcome, t.entry_price, t.size_usd, t.status, m.title 
                FROM bot_trades t
                LEFT JOIN markets m ON t.condition_id = m.condition_id
                WHERE t.status IN ('OPEN', 'SIMULATED_OPEN')
            """)
            trades = await cursor.fetchall()
            
            if not trades:
                return

            console.print(f"[dim]Tracking PnL for {len(trades)} active trades...[/dim]")
            
            for trade in trades:
                condition_id = trade['condition_id']
                outcome = trade['outcome']
                entry_price = trade['entry_price']
                status = trade['status']
                title = trade['title'] or "Unknown Market"
                
                # Resolve token_id
                yes_id, no_id = await MarketAPI.get_token_ids(condition_id)
                token_id = None
                
                if outcome.upper() == 'YES':
                    token_id = yes_id
                elif outcome.upper() == 'NO':
                    token_id = no_id
                
                if not token_id:
                    continue
                
                # Get Current Price
                current_price = await MarketAPI.get_market_price(token_id)
                
                if current_price > 0:
                    # Calculate Unrealized PnL %
                    if entry_price > 0:
                        pnl_percent = ((current_price - entry_price) / entry_price) * 100
                        color = "green" if pnl_percent >= 0 else "red"
                        # Truncate title for display if too long
                        # display_title = (title[:40] + '..') if len(title) > 40 else title
                        display_title = title # Show full title to avoid confusion
                        console.print(f"  • {display_title} [{outcome}] ({status})")
                        console.print(f"    Entry ${entry_price:.2f} ➜ Now ${current_price:.2f} | PnL: [{color}]{pnl_percent:+.2f}%[/]")

                        
                        # Here we could update the DB if we had a column for current_price or unrealized_pnl
                        # For now, just logging to console as "Tracking"
                        
                        # Update DB with current unrealized PnL (optional, requires schema update)
                        # We will just print it for now as requested.
                        
                        # Use Database.update_bot_pnl (placeholder logic)
                        # To properly implement, we might want to store the last_price in the DB or calculate PnL there
                        # but performance.py is doing the heavy lifting here.
                        # Ideally, Database class should handle DB writes.
                        
                        # Calculate realized/unrealized PnL value in USD
                        # If trade is BUY: PnL = (current_price - entry_price) * (size_usd / entry_price) 
                        # size_usd is the initial investment. shares = size_usd / entry_price
                        shares = trade['size_usd'] / entry_price if entry_price > 0 else 0
                        current_value = shares * current_price
                        pnl_usd = current_value - trade['size_usd']
                        
                        console.print(f"    Value: ${current_value:.2f} (Inv: ${trade['size_usd']:.2f}) | PnL: ${pnl_usd:.2f}")

                # Check for resolution (Implement if needed, requires checking CTF or resolved status)
