import asyncio
import time
import logging
import json
import aiohttp
from .database import Database
from .notifier import Notifier
from .config import Config
from .trader import Trader

logger = logging.getLogger("SniperStrategy")

class SniperStrategy:
    def __init__(self, live_markets_state: dict):
        self.live_markets = live_markets_state
        self.notifier = Notifier()
        self.trader = Trader() if getattr(Config, "SNIPER_LIVE_TRADE", False) else None
        self.sl_triggered = set()

    async def sniper_execution_loop(self):
        """Iterates through live_markets to execute paper trades exactly N seconds before expiration."""
        logger.info("Started Late-Stage Sniper trading loop.")
        while True:
            await asyncio.sleep(0.5)
            current_time = time.time()
            
            triggered_trades = []
            
            for market_id, data in list(self.live_markets.items()):
                if data["traded"]:
                    sl = getattr(Config, "SNIPER_SL", 0.0)
                    if sl > 0.0 and market_id not in self.sl_triggered:
                        buy_price = data.get("buy_price")
                        best_bid = data.get("best_bid")
                        best_ask = data.get("best_ask")
                        
                        exp_time = data["expiration_timestamp"]
                        time_left = exp_time - current_time
                        
                        # ANTI-LIQUIDITY DRAIN PROTECTIONS
                        # 1. We consider the market in RESOLUTION if time_left < 15.0 or negative.
                        # Do not execute stop loss during resolution because orderbook is cleared.
                        if time_left < 0:
                            continue
                            
                        # 2. Prevent Stop Loss on artificially bad quotes if spread goes huge
                        if best_bid is not None and best_ask is not None:
                            spread = best_ask - best_bid
                            if spread > 0.15: # if spread is > 15 cents, orderbook is ghosted
                                continue

                        if buy_price and best_bid is not None:
                            # SL is an absolute drop or price floor. Let's use a percentage drop: 
                            # Let's trigger if `best_bid <= buy_price - sl`. If buy is 0.80 and SL is 0.20, we sell at 0.60. This is the most logical "amount to lose".
                            # core SL
                            if best_bid <= (buy_price - sl):
                                self.sl_triggered.add(market_id)
                                question = data.get("question", "Unknown Market")
                                outcome = data.get("outcome", "Unknown Option")
                                
                                # Close the paper trade in the database
                                trade_id, realized_pnl = await Database.close_paper_trade_by_asset(market_id, best_bid)
                                pnl_str = f"${realized_pnl:.3f}" if realized_pnl else "N/A"
                                
                                logger.info(f"STOP LOSS HIT for {market_id} ({outcome})! Buy: {buy_price}, Current Bid: {best_bid}, SL drop: {sl}, PNL: {pnl_str}")
                                msg = f"🛑 **STOP LOSS TRIGGERED**\nMarket: `{question}`\nOutcome: `{outcome}`\nToken: `{market_id}`\nBuy Price: ${buy_price:.3f}\nExit Bid: ${best_bid:.3f}\nRealized PNL: {pnl_str}"
                                asyncio.create_task(self.notifier.send_alert(msg))
                                
                                if data.get("live_trade_executed") and self.trader:
                                    asyncio.create_task(self.trader.execute_copy_trade(market_id, "Sniper Target", 0, "SELL"))
                                    
                    continue
                
                exp_time = data["expiration_timestamp"]
                best_ask = data["best_ask"]
                
                if best_ask is None:
                    continue
                
                time_left = exp_time - current_time
                trigger_time = Config.SNIPER_TRIGGER_SECONDS
                
                # We trigger within a 2-second window
                #Core execution
                if (trigger_time - 1.0) <= time_left <= (trigger_time + 1.0):
                    if 0.75 <= best_ask <= 0.99:
                        self.live_markets[market_id]["traded"] = True
                        self.live_markets[market_id]["buy_price"] = best_ask
                        self.live_markets[market_id]["live_trade_executed"] = getattr(Config, "SNIPER_LIVE_TRADE", False)
                        seconds_left_int = int(time_left)
                        question = data.get("question", "Unknown Market")
                        outcome = data.get("outcome", "Unknown Option")
                        
                        logger.info(f"SNIPER TRIGGERED! Market: {question} | Outcome: {outcome} | Ask: {best_ask} | Time Left: {time_left:.2f}s")
                        
                        triggered_trades.append(
                            (current_time, market_id, best_ask, seconds_left_int, getattr(Config, "SNIPER_LIVE_TRADE", False))
                        )
                        
                        msg_prefix = "🚀 **REAL SNIPER TRADE**" if getattr(Config, "SNIPER_LIVE_TRADE", False) else "🎯 **SNIPER PAPER TRADE**"
                        msg = f"{msg_prefix}\nMarket: `{question}`\nOutcome: `{outcome}`\nToken: `{market_id}`\nAsk Price: ${best_ask:.3f}\nTime Left: {time_left:.1f}s"
                        asyncio.create_task(self.notifier.send_alert(msg))
                        
                        if getattr(Config, "SNIPER_LIVE_TRADE", False) and self.trader:
                            asyncio.create_task(self.trader.execute_copy_trade(market_id, "Sniper Target", best_ask, "BUY"))
            
            for trade in triggered_trades:
                await Database.log_paper_trade(trade[0], trade[1], trade[2], trade[3], trade[4])

    async def pnl_resolution_loop(self):
        """Periodically checks unresolved paper trades and updates their PNL using the Gamma API."""
        while True:
            await asyncio.sleep(60)
            try:
                unresolved = await Database.get_unresolved_paper_trades()
                
                if not unresolved:
                    continue

                async with aiohttp.ClientSession() as session:
                    for rowid, asset_id, buy_price, parent_market_id in unresolved:
                        if not parent_market_id:
                            continue
                            
                        url = f"https://gamma-api.polymarket.com/markets/{parent_market_id}"
                        
                        try:
                            await asyncio.sleep(1) # ratelimit padding
                            async with session.get(url) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    if data.get("closed"):
                                        token_prices = data.get("outcomePrices", "[]")
                                        if isinstance(token_prices, str):
                                            token_prices = json.loads(token_prices)
                                        
                                        clobTokenIds = data.get("clobTokenIds", "[]")
                                        if isinstance(clobTokenIds, str):
                                            clobTokenIds = json.loads(clobTokenIds)
                                        
                                        idx = clobTokenIds.index(asset_id) if asset_id in clobTokenIds else -1
                                        if idx != -1 and idx < len(token_prices):
                                            resolved_price = float(token_prices[idx])
                                            if resolved_price == 1.0 or resolved_price == 0.0:
                                                pnl = resolved_price - buy_price
                                                await Database.update_paper_trade_pnl(rowid, pnl, resolved_price)
                                                logger.info(f"Resolved trade for {asset_id}: PNL = {pnl:.2f}, outcome = {resolved_price}")
                                                
                                                msg = f"🏁 **SNIPER TRADE RESOLVED**\nMarket: `{asset_id}`\nPNL: ${pnl:.2f}\nOutcome: {resolved_price}"
                                                asyncio.create_task(self.notifier.send_alert(msg))
                        except Exception as e:
                            logger.error(f"Error fetching resolution for {parent_market_id}: {e}")
            except Exception as e:
                logger.error(f"Error in PNL loop: {e}")
