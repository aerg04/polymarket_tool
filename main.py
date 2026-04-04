import asyncio
import sys
import time
import logging
from rich.console import Console
from rich.panel import Panel

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from src.config import Config
from src.tracker import Tracker
from src.ws_client import WSClient
from src.sniper import SniperStrategy
from src.notifier import Notifier
from src.trader import Trader
from src.database import Database
from src.market_api import MarketAPI
from src.redeemer import Redeemer
from src.performance import PerformanceTracker

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
    if condition_id:
        condition_id = condition_id.lower()
    
    side = act.get('side', 'UNKNOWN')
    size = float(act.get('size', 0))
    price = float(act.get('price', 0))
    # value = size * price
    title = act.get('title', 'Unknown Market')
    outcome = act.get('outcome', '-')
    if outcome:
        outcome = str(outcome).strip() # Ensure clean string
    timestamp = int(act.get('timestamp', time.time()))
    
    # 1. Analyze the Activity
    console.print(Panel(f"Processing Activity: {act_id}", title="Whale Activity Detected", style="bold magenta"))
    
    # Detailed console log (replaces the tracker's internal log)
    console.print(f"[cyan]🕵️  Wallet: {wallet}[/cyan]")
    console.print(f"[white]📊 Market: {title}[/white]")
    console.print(f"[green]⚡ Action: {side} {size:,.2f} shares of '{outcome}' @ ${price:.3f}[/green]")

    # Initialize token IDs
    token_id_yes = None
    token_id_no = None

    # --- SAVE TO DB ---
    # We must ensure we have a condition_id. The API should provide it. 
    # If using tracker.py, it is extracted as 'conditionId'.
    
    if condition_id:
        # Fetch token IDs (YES/NO) from Gamma API
        token_id_yes, token_id_no = await MarketAPI.get_token_ids(condition_id)
        
        await Database.log_whale_activity(
            wallet=wallet, 
            condition_id=condition_id,
            token_id_yes=token_id_yes,
            token_id_no=token_id_no,
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
    # Trade both BUY and SELL
    if side in ["BUY", "SELL"]:
        
        # Determine the correct token_id to trade based on outcome
        if outcome.lower() == "yes" and token_id_yes:
            trade_token_id = token_id_yes
        elif outcome.lower() == "no" and token_id_no:
            trade_token_id = token_id_no
        else:
            # Fallback to whatever 'asset' was in the activity, or None
            trade_token_id = act.get('asset') or act.get('asset_id')

        # We pass 'outcome' or 'asset' as token_id for now since API might not give raw ID
        token_identifier = f"{title} [{outcome}]" 
        
        if trade_token_id:
            # SIMULATION logic moved here
            if Config.SIMULATION_MODE:
                console.print(f"[bold blue]SIMULATION: Skipping CLOB execution for {token_identifier}[/bold blue]")
                
                sim_amount = 0.0
                if side.upper() == "BUY":
                    # We need to know how much we would have bet.
                    # Trader has logic for this, we can reuse it if we instantiate Trader or move logic.
                    # For now, let's just use the bet amount from config directly for simplicity or ask trader
                    sim_amount = await trader.calculate_bet_size()
                else:
                    # For SELL in simulation, we must know how much 'simulated' inventory we have.
                    # We can query the DB for the open position size.
                    open_trade = await Database.find_open_trade(condition_id, outcome)
                    if open_trade:
                         sim_amount = open_trade[2] # size_usd (Wait, size_usd is USD value, not shares)
                         # We need shares. If we stored size_usd and entry_price, shares = size_usd / entry_price
                         entry_price = open_trade[1]
                         if entry_price > 0:
                             sim_amount = (open_trade[2] / entry_price) # Approximate shares
                    else:
                        sim_amount = 0

                estimated_price = price if price > 0 else 0.5 
                
                if side.upper() == 'BUY':
                    # Limit to one open trade per market/outcome (simple logic)
                    existing = await Database.find_open_trade(condition_id, outcome or '?')
                    if not existing:
                        await Database.log_bot_trade(
                            condition_id=condition_id,
                            outcome=outcome or '?',
                            side=side,
                            entry_price=estimated_price,
                            size_usd=sim_amount,
                            status='SIMULATED_OPEN'
                        )
                    else:
                         console.print(f"[yellow]Simulation: Trade already open for {outcome}. Skipping duplicate log.[/yellow]")
                elif side.upper() == 'SELL':
                     # Find existing OPEN trade to close
                     existing = await Database.find_open_trade(condition_id, outcome or '?')
                     if existing:
                         trade_id = existing[0]
                         await Database.close_bot_trade(trade_id, exit_price=estimated_price)
                     else:
                         console.print(f"[yellow]Simulation: No OPEN trade found to close for: {title} [{outcome}][/yellow]")
                         console.print(f"[dim](Searched Condition ID: {condition_id})[/dim]")

            else:
                # REAL EXECUTION
                await trader.execute_copy_trade(
                    token_id=trade_token_id, 
                    target_name=token_identifier, 
                    original_amount=size, 
                    side=side,
                    condition_id=condition_id,
                    outcome=outcome
                )
        else:
            console.print(f"[red]Could not determine token_id for trade on {token_identifier}[/red]")

async def main():
    console.print(Panel("Polymarket Copy Trading Bot", subtitle="v2.0.0", style="bold green"))
    
    # 1. Validate Config
    if not Config.validate():
        sys.exit(1)
    await Database.init_db()  # Initialize the database (creates tables if not exist)
    
    tasks = []

    if Config.ENABLE_WHALE_TRACKER:
        console.print("[green]Whale Tracker Enabled[/green]")
        # 2. Initialize Modules
        tracker = Tracker(process_transaction_callback=process_whale_activity)
        
        # Run a redemption check on startup
        trader = Trader()
        redeemer = Redeemer(trader)
        
        console.print("[yellow]Checking for redeemable positions...[/yellow]")
        # We create a task for this so it runs async but doesn't block main loop if slow
        asyncio.create_task(redeemer.check_and_redeem())
        
        # Start Performance Tracker
        tracker_pnl = PerformanceTracker()
        asyncio.create_task(tracker_pnl.run())

        tasks.append(asyncio.create_task(tracker.start_monitoring()))

    if Config.ENABLE_WS_GATHERING or Config.ENABLE_BTC_SNIPER:
        console.print("[green]WebSocket Data Gathering Enabled[/green]")
        # Shared state for real-time prices
        live_markets_state = {} 
        
        ws_client = WSClient(live_markets_state)
        # Connect WS & fetch initial tokens
        tasks.append(asyncio.create_task(ws_client.connect()))
        tasks.append(asyncio.create_task(ws_client.update_subscriptions_loop()))

        if Config.ENABLE_BTC_SNIPER:
            console.print("[green]BTC Sniper bot Enabled[/green]")
            sniper = SniperStrategy(live_markets_state)
            # Start Sniper trading & PnL tracking
            tasks.append(asyncio.create_task(sniper.sniper_execution_loop()))
            tasks.append(asyncio.create_task(sniper.pnl_resolution_loop()))

    if not tasks:
        console.print("[bold red]No strategies enabled. Exiting.[/bold red]")
        return

    # 3. Start Loop
    try:
        await asyncio.gather(*tasks)
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
            console.print("Event loop already running. Please assume main() is scheduled.")
            # In a real script execution, this won't happen. 
            # But just in case, we can use create_task if we were inside another async context.
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        pass
