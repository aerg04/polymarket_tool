import asyncio
import sys
import time
from rich.console import Console
from rich.panel import Panel

from src.config import Config
from src.tracker import Tracker
from src.notifier import Notifier
from src.trader import Trader
from src.database import Database

console = Console()

async def process_whale_activity(act):
    """
    Callback function triggered when the Tracker detects a relevant activity.
    Receives an 'activity' dictionary from the Polymarket API.
    """
    notifier = Notifier()
    trader = Trader()
    
    # Extract data from API activity object
    act_id = act.get('asset', 'unknown_id')
    wallet = act.get('wallet_address', 'unknown_wallet')
    
    # Extract the REAL conditionId
    condition_id = act.get('conditionId')
    
    side = act.get('side', 'UNKNOWN')
    size = float(act.get('size', 0))
    price = float(act.get('price', 0))
    # value = size * price
    title = act.get('title', 'Unknown Market')
    outcome = act.get('outcome', '-')
    timestamp = int(act.get('timestamp', time.time()))
    
    # 1. Analyze the Activity
    console.print(Panel(f"Processing Activity: {act_id}", title="Whale Activity Detected", style="bold magenta"))
    
    # detailed console log (replaces the tracker's internal log)
    console.print(f"[cyan]🕵️  Wallet: {wallet}[/cyan]")
    console.print(f"[white]📊 Market: {title}[/white]")
    console.print(f"[green]⚡ Action: {side} {size:,.2f} shares of '{outcome}' @ ${price:.3f}[/green]")

    # --- SAVE TO DB ---
    # We must ensure we have a condition_id. The API should provide it. 
    # If using tracker.py, it is extracted as 'conditionId'.
    token_id = act.get('asset_id') or act.get('token_id')
    
    if condition_id:
        await Database.log_whale_activity(
            wallet=wallet, 
            condition_id=condition_id,
            token_id=token_id,
            title=title, 
            outcome=outcome, 
            side=side, 
            size=size, 
            price=price,
            timestamp=timestamp
        )
    else:
        console.print("[red]⚠️ Skipping DB log: No conditionId found in activity[/red]")
    
    # ------------------

    # 2. Notify
    msg = f"🐋 **WHALE ALERT**\nAddress: `{wallet}`\nAction: {side} {outcome}\nMarket: {title}\nPrice: ${price:.3f}\nSize: {size}"
    await notifier.send_alert(msg)
    
    # 3. Trade
    # Only trade if it's a BUY/SELL (API usually ensures this with type='TRADE')
    if side == "BUY":
        # We pass 'outcome' or 'asset' as token_id for now since API might not give raw ID
        token_identifier = f"{title} [{outcome}]" 
        await trader.execute_copy_trade(token_id=act_id,target_name = token_identifier, original_amount=size, side="BUY")

async def main():
    console.print(Panel("Polymarket Copy Trading Bot", subtitle="v1.0.0", style="bold green"))
    
    # 1. Validate Config
    if not Config.validate():
        sys.exit(1)
    await Database.init_db()  # Initialize the database (creates tables if not exist)
    # 2. Initialize Modules
    tracker = Tracker(process_transaction_callback=process_whale_activity)
    
    # 3. Start Loop
    try:
        await tracker.start_monitoring()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Shutting down...[/bold yellow]")
    except Exception as e:
        console.print(f"[bold red]Fatal Error: {e}[/bold red]")

if __name__ == "__main__":
    try:
        # Check if we're running in an environment with an event loop already (like Jupyter)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            print("Event loop already running. Please assume main() is scheduled.")
            # In a real script execution, this won't happen. 
            # But just in case, we can use create_task if we were inside another async context.
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        pass
