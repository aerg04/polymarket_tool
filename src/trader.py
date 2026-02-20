import asyncio
import requests
from src.config import Config, console
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL

class Trader:
    def __init__(self):
        self.wallet_address = Config.MY_WALLET_ADDRESS
        self.private_key = Config.PRIVATE_KEY
        self.default_bet_size = Config.BET_AMOUNT_USDC
        self.bet_percentage = Config.BET_PERCENTAGE
        self.mode = Config.BET_MODE
        
        # Authentication settings from config
        self.signature_type = Config.SIGNATURE_TYPE
        self.funder_address = Config.FUNDER_ADDRESS

        # Initialize Polymarket CLOB Client
        self.client = None
        if self.private_key:
            try:
                self.client = ClobClient(
                    Config.POLYMARKET_CLOB_API_URL,
                    key=self.private_key,
                    chain_id=137, # Polygon Mainnet
                    signature_type=self.signature_type,
                    funder=self.funder_address
                )
                
                # Derive L2 API Key if needed
                try:
                    creds = self.client.derive_api_key()
                    self.client.set_api_creds(creds)
                    console.print(f"[green]✔ Authenticated with Polymarket CLOB[/green]")
                except Exception as e:
                    console.print(f"[yellow]⚠ API Key Derivation skipped/failed: {e}[/yellow]")
            
            except Exception as e:
                console.print(f"[bold red]✘ Failed to initialize ClobClient:[/bold red] {e}")

    async def get_wallet_balance(self):
        """
        Fetches the current USDC balance/allowance on the Exchange.
        """
        if not self.client:
            # Fallback mock if no client (for testing without keys)
            return 2000.0 

        try:
            # Fetch collateral balance (USDC)
            balance_info = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            # Balance is in atomic units (6 decimals for USDC)
            raw_balance = float(balance_info['balance'])
            usdc_balance = raw_balance / 1e6
            return usdc_balance
        except Exception as e:
            console.print(f"[red]Error fetching balance: {e}[/red]")
            return 0.0

    async def calculate_bet_size(self):
        """Calculates the bet size based on configuration."""
        if self.mode == "PERCENTAGE":
            balance = await self.get_wallet_balance()
            bet_size = balance * self.bet_percentage
            console.print(f"[cyan]Calculating Bet: {self.bet_percentage*100:.1f}% of {balance:.2f} USDC = {bet_size:.2f} USDC[/cyan]")
            return bet_size
        else:
            return self.default_bet_size

    async def execute_copy_trade(self, token_id, original_amount: float, side: str):
        """
        Executes a trade on Polymarket matching the whale's activity.
        
        Args:
            token_id: The ID or Name of the outcome token (CLOB Token ID).
            original_amount: The amount the whale bet.
            side: 'BUY' or 'SELL'.
        """
        bet_amount = await self.calculate_bet_size()

        console.print(f"[bold yellow]Executing COPY TRADE...[/bold yellow]")
        console.print(f"Target Market/Token: {token_id}")
        console.print(f"My Bet Size: {bet_amount:.2f} USDC (Whale size: {original_amount})")

        if not self.client:
            console.print("[bold red]✘ Client not initialized. Cannot trade.[/bold red]")
            return False

        try:
            order_side = BUY if side.upper() == 'BUY' else SELL
            
            # Using Fill-Or-Kill (FOK) Market Order
            # For BUY, amount usually refers to the amount of collateral (USDC) you want to spend. 
            # For SELL, it might refer to the amount of shares you want to sell.
            # Assuming 'amount' here is in USDC value if BUY, and Shares if SELL.
            # However, for copy trading simpler heuristic: we mimic the whale's conviction translated to our size.
            
            # Note: py-clob-client create_market_order uses 'amount'.
            market_order = MarketOrderArgs(
                token_id=token_id,
                amount=bet_amount, 
                side=order_side,
                order_type=OrderType.FOK 
            )

            # create_market_order returns a SignedOrder
            signed_order = self.client.create_market_order(market_order)
            
            # Execute the order
            resp = self.client.post_order(signed_order, OrderType.FOK)
            
            console.print(f"[bold green]✔ Trade Executed Successfully![/bold green]")
            console.print(f"Order ID: {resp.get('orderID', 'Unknown')}")
            return True

        except Exception as e:
            console.print(f"[bold red]✘ Trade Failed:[/bold red] {e}")
            return False

    def get_bot_positions(self):
        """
        Retrieves the bot's current positions using the Polymarket Data API.
        """
        if not self.wallet_address:
            console.print("[red]Wallet address not configured.[/red]")
            return []

        try:
            url = f"{Config.POLYMARKET_DATA_API_URL}/positions"
            params = {"user": self.wallet_address}
            
            console.print(f"[cyan]Fetching positions for {self.wallet_address}...[/cyan]")
            response = requests.get(url, params=params)
            response.raise_for_status()
            
            positions = response.json()
            
            # Filter for active positions (size > 0)
            # The API returns positions with 'size' as string usually
            active_positions = []
            for p in positions:
                size = float(p.get('size', 0))
                if size > 0.0001: # Filter empty/dust
                    p['float_size'] = size
                    active_positions.append(p)
            
            console.print(f"[green]Found {len(active_positions)} active positions.[/green]")
            for p in active_positions:
                title = p.get('title', 'Unknown Market')
                outcome = p.get('outcome', '?')
                size = p.get('float_size')
                value = p.get('currentValue', 0)
                console.print(f"  • {title} [{outcome}] | Size: {size:.2f} | Value: ${value}")
                
            return active_positions

        except Exception as e:
            console.print(f"[bold red]✘ Failed to fetch positions:[/bold red] {e}")
            return []
