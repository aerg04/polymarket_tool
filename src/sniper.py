import asyncio
import time
import logging
import json
import aiohttp
from .database import Database
from .notifier import Notifier
from .config import Config

logger = logging.getLogger("SniperStrategy")

class SniperStrategy:
    def __init__(self, live_markets_state: dict):
        self.live_markets = live_markets_state
        self.notifier = Notifier()

    async def sniper_execution_loop(self):
        """Iterates through live_markets to execute paper trades exactly N seconds before expiration."""
        logger.info("Started Late-Stage Sniper trading loop.")
        while True:
            await asyncio.sleep(0.5)
            current_time = time.time()
            
            triggered_trades = []
            
            for market_id, data in list(self.live_markets.items()):
                if data["traded"]:
                    continue
                
                exp_time = data["expiration_timestamp"]
                best_ask = data["best_ask"]
                
                if best_ask is None:
                    continue
                
                time_left = exp_time - current_time
                trigger_time = Config.SNIPER_TRIGGER_SECONDS
                
                # We trigger within a 2-second window
                if (trigger_time - 1.0) <= time_left <= (trigger_time + 1.0):
                    if 0.75 <= best_ask <= 0.99:
                        self.live_markets[market_id]["traded"] = True
                        seconds_left_int = int(time_left)
                        logger.info(f"SNIPER TRIGGERED! Market: {market_id} | Ask: {best_ask} | Time Left: {time_left:.2f}s")
                        
                        triggered_trades.append(
                            (current_time, market_id, best_ask, seconds_left_int)
                        )
                        
                        msg = f"🎯 **SNIPER PAPER TRADE**\nMarket: `{market_id}`\nAsk Price: ${best_ask:.3f}\nTime Left: {time_left:.1f}s"
                        asyncio.create_task(self.notifier.send_alert(msg))
            
            for trade in triggered_trades:
                await Database.log_paper_trade(trade[0], trade[1], trade[2], trade[3])

    async def pnl_resolution_loop(self):
        """Periodically checks unresolved paper trades and updates their PNL using the Gamma API."""
        while True:
            await asyncio.sleep(60)
            try:
                unresolved = await Database.get_unresolved_paper_trades()
                
                if not unresolved:
                    continue

                async with aiohttp.ClientSession() as session:
                    for rowid, asset_id, buy_price in unresolved:
                        parent_market_id = await Database.get_parent_market_id(asset_id)
                        if not parent_market_id:
                            continue
                            
                        url = f"https://gamma-api.polymarket.com/markets/{parent_market_id}"
                        
                        try:
                            await asyncio.sleep(1) # ratelimit padding
                            async with session.get(url) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    if data.get("closed") and data.get("endDate"):
                                        token_prices = data.get("prices", "[]")
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
