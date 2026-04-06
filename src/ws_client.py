import asyncio
import json
import logging
import time
import websockets
import aiohttp
from websockets.exceptions import ConnectionClosed
from .database import Database
from .config import Config

logger = logging.getLogger("PolymarketWS")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PLACEHOLDER_TOKEN = "PLACEHOLDER_TOKEN"

class WSClient:
    def __init__(self, live_markets_state: dict):
        self.live_markets = live_markets_state
        self.websocket = None
        self.active_tokens = set()

    async def connect(self):
        """Connect to WebSocket with automatic reconnection."""
        while True:
            try:
                logger.info(f"Connecting to {WS_URL}...")
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as websocket:
                    self.websocket = websocket
                    logger.info("Connected directly to Polymarket CLOB.")
                    
                    # Ensure placeholder is active if no tokens
                    if not self.active_tokens:
                        self.active_tokens.add(PLACEHOLDER_TOKEN)
                        
                    if self.active_tokens:
                        await self.subscribe(list(self.active_tokens))
                    await self._listen()
            except ConnectionClosed as e:
                logger.warning(f"WebSocket closed: {e}. Reconnecting in 5 seconds...")
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting in 5 seconds...")
            finally:
                self.websocket = None
            
            await asyncio.sleep(5)

    async def subscribe(self, tokens: list):
        """Send subscription payload for dynamic tokens."""
        self.active_tokens.update(tokens)
        if not self.websocket:
            return
            
        valid_tokens = [t for t in tokens if t != PLACEHOLDER_TOKEN]
        if not valid_tokens:
            return
            
        payload = {
            "assets_ids": valid_tokens,
            "action": "subscribe",
            "custom_feature_enabled": True
        }
        await self.websocket.send(json.dumps(payload))
        logger.info(f"Sent subscription for {len(valid_tokens)} tokens")

    async def unsubscribe(self, tokens: list):
        """Send unsubscription payload."""
        for t in tokens:
            self.active_tokens.discard(t)
            
        if not self.websocket:
            return
            
        valid_tokens = [t for t in tokens if t != PLACEHOLDER_TOKEN]
        if not valid_tokens:
            return
            
        payload = {
            "assets_ids": valid_tokens,
            "action": "unsubscribe"
        }
        await self.websocket.send(json.dumps(payload))
        logger.info(f"Sent unsubscription for {len(valid_tokens)} tokens")

    async def _handle_event_wrapper(self, data):
        try:
            await self._handle_event(data)
        except Exception as e:
            logger.error(f"Error handling WS event: {e}")

    async def _listen(self):
        """Listen for incoming messages and route them."""
        async for message in self.websocket:
            try:
                data = json.loads(message)
                #print(f"Received WS message: {data}")
                if isinstance(data, list):
                    for item in data:
                        asyncio.create_task(self._handle_event_wrapper(item))
                else:
                    asyncio.create_task(self._handle_event_wrapper(data))
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON: {message}")
            except Exception as e:
                logger.error(f"Error handling message: {e}")

    async def _handle_event(self, data):
        """Parse event_type and insert into the database."""
        event_type = data.get("event_type")

        if not event_type:
            return

        ts = time.time()
        market_id = data.get("asset_id") or data.get("market") or data.get("market_id") or "UNKNOWN"

        if self.active_tokens and market_id not in self.active_tokens and market_id != "UNKNOWN":
             return

        if event_type == "best_bid_ask":
            best_bid = data.get("best_bid")
            best_ask = data.get("best_ask")
            
            try:
                best_bid = float(best_bid) if best_bid is not None else None
                best_ask = float(best_ask) if best_ask is not None else None
                spread = best_ask - best_bid if best_ask is not None and best_bid is not None else None
            except ValueError:
                spread = None

            if market_id in self.live_markets:
                if best_ask is not None:
                    self.live_markets[market_id]["best_ask"] = best_ask
                if best_bid is not None:
                    self.live_markets[market_id]["best_bid"] = best_bid

            if Config.LOG_WS_EVENTS:
                await Database.log_best_bid_ask(ts, market_id, best_bid, best_ask, spread)

        elif event_type == "last_trade_price":
            price = data.get("price")
            side = data.get("side")
            size = data.get("size")
            if Config.LOG_WS_EVENTS:
                await Database.log_trade(ts, market_id, price, side, size)

        elif event_type == "price_change":
            changes = data.get("price_changes", [])
            for change in changes:
                price = change.get("price")
                size = change.get("size")
                side = change.get("side")
                item_market_id = change.get("asset_id") or market_id
                
                if self.active_tokens and item_market_id not in self.active_tokens and item_market_id != "UNKNOWN":
                    continue

                if Config.LOG_WS_EVENTS:
                    await Database.log_price_change(ts, item_market_id, price, size, side)

        elif event_type == "tick_size_change":
            old_tick = data.get("old_tick_size")
            new_tick = data.get("new_tick_size")
            if Config.LOG_WS_EVENTS:
                await Database.log_tick_size_change(ts, market_id, old_tick, new_tick)
            
    async def fetch_crypto_tokens(self) -> list:
        tokens = []
        current_time = time.time()
        current_window_epoch = int(current_time // 300 * 300)
        
        intervals_ahead = 48
        epochs_to_fetch = [current_window_epoch + (i * 300) for i in range(intervals_ahead)]
        
        async with aiohttp.ClientSession() as session:
            for epoch in epochs_to_fetch:
                url = f"https://gamma-api.polymarket.com/events/slug/btc-updown-5m-{epoch}"
                
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            for market in data.get("markets", []):
                                token_ids_str = market.get("clobTokenIds")
                                question = market.get("question", "")
                                if token_ids_str:
                                    try:
                                        market_tokens = json.loads(token_ids_str)
                                        outcomes = json.loads(market.get("outcomes", "[]"))
                                        parent_market_id = market.get("id", "")
                                        tokens.extend(market_tokens)
                                        
                                        for i, token_id in enumerate(market_tokens):
                                            outcome = outcomes[i] if i < len(outcomes) else str(i)
                                            if token_id not in self.live_markets:
                                                self.live_markets[token_id] = {
                                                    "expiration_timestamp": epoch + 300,
                                                    "best_ask": None,
                                                    "best_bid": None,
                                                    "traded": False,
                                                    "parent_market_id": parent_market_id,
                                                    "question": question,
                                                    "outcome": outcome,
                                                }
                                            await Database.log_ws_market(token_id, parent_market_id, question, outcome, current_time)
                                    except json.JSONDecodeError:
                                        pass
                except Exception as e:
                    logger.error(f"Error fetching from Gamma API: {e}")
            
        filtered_tokens = list(set([t for t in tokens if t and isinstance(t, str)]))
        logger.info(f"Loaded {len(filtered_tokens)} token IDs for rolling sequence.")
        return filtered_tokens

    async def update_subscriptions_loop(self):
        # Wait a bit before initial fetch
        await asyncio.sleep(2)
        initial_tokens = await self.fetch_crypto_tokens()
        if initial_tokens:
            await self.subscribe(initial_tokens)
            
        while True:
            await asyncio.sleep(7200) 
            logger.info("Periodic refresh: Fetching upcoming crypto tokens...")
            new_tokens = await self.fetch_crypto_tokens()
            
            tokens_to_add = [t for t in new_tokens if t not in self.active_tokens]
            if tokens_to_add:
                logger.info(f"Found {len(tokens_to_add)} new tokens. Subscribing...")
                await self.subscribe(tokens_to_add)

            new_tokens_set = set(new_tokens)
            tokens_to_remove = [t for t in self.active_tokens if t not in new_tokens_set and t != PLACEHOLDER_TOKEN]
            if tokens_to_remove:
                logger.info(f"Found {len(tokens_to_remove)} old tokens. Unsubscribing...")
                await self.unsubscribe(tokens_to_remove)
