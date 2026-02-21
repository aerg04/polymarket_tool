import asyncio
from web3 import Web3
from src.config import Config, console
from src.trader import Trader

# ABI for Gnosis Conditional Tokens Framework (CTF)
CTF_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "index", "type": "uint256"}
        ],
        "name": "payoutNumerators",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"}
        ],
        "name": "redeemPositions",
        "outputs": [],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174" # Polygon USDC (Bridged)

class Redeemer:
    def __init__(self, trader: Trader):
        self.trader = trader
        self.w3 = None
        
        if Config.POLYGON_RPC_URL:
            try:
                self.w3 = Web3(Web3.HTTPProvider(Config.POLYGON_RPC_URL))
                if self.w3.is_connected():
                    console.print("[green]✔ Connected to Polygon RPC for redemption.[/green]")
                else:
                    console.print("[red]✘ Failed to connect to Polygon RPC.[/red]")
            except Exception as e:
                console.print(f"[red]Error initializing Web3: {e}[/red]")
        else:
            console.print("[yellow]⚠ POLYGON_RPC_URL not set. Redemption disabled.[/yellow]")

    
    async def check_and_redeem(self):
        """
        Checks all held positions. If a market is resolved, redeems the winnings.
        """
        if not self.w3:
            console.print("[yellow]Redemption skipped: No Web3[/yellow]")
            return

        # Fetch positions (synchronous in trader, wrap in future/executor if needed, but requests is ok for now if fast)
        # Actually Trader.get_bot_positions should be async ideally but it is sync requests.
        # Let's wrap it in thread if blocking, but for startup it's fine.
        positions = self.trader.get_bot_positions()
        
        if not positions:
            console.print("[dim]No positions to check for redemption.[/dim]")
            return

        # Group by Condition ID to avoid duplicate checks
        conditions_map = {}
        for p in positions:
            cid = p.get('conditionId')
            if cid:
                if cid not in conditions_map:
                    conditions_map[cid] = []
                conditions_map[cid].append(p)

        console.print(f"[cyan]Checking {len(conditions_map)} unique markets for resolution...[/cyan]")
        
        ctf = self.w3.eth.contract(address=Config.POLYMARKET_CTF_CONTRACT, abi=CTF_ABI)
        
        for condition_id, pos_list in conditions_map.items():
            try:
                # Check if resolved (payoutNumerators > 0 for any index)
                # Binary market usually has 2 slots. Check index 0.
                is_resolved = False
                payouts = []
                
                # We check index 0 and 1 (assuming binary for now, likely safe for Polymarket)
                p0 = ctf.functions.payoutNumerators(condition_id, 0).call()
                p1 = ctf.functions.payoutNumerators(condition_id, 1).call()
                
                if p0 > 0 or p1 > 0:
                    is_resolved = True
                    payouts = [p0, p1]
                    console.print(f"[green]✔ Market Resolved! Condition: {condition_id[:10]}... Payouts: {payouts}[/green]")
                
                if is_resolved:
                    # Proceed to redeem
                    await self.redeem_positions(ctf, condition_id, pos_list)
                else:
                    # console.print(f"[dim]Market not resolved: {condition_id[:10]}...[/dim]")
                    pass
                    
            except Exception as e:
                console.print(f"[red]Error checking condition {condition_id}: {e}[/red]")

    async def redeem_positions(self, ctf, condition_id, pos_list):
        """
        Executes the redeemPositions transaction on CTF.
        """
        if not Config.MY_WALLET_ADDRESS or not Config.PRIVATE_KEY:
            console.print("[red]Cannot redeem: Missing wallet/key.[/red]")
            return

        wallet_address = Config.MY_WALLET_ADDRESS # Assuming EOA directly for signing
        # If using Proxy/Magic, we need executeCall on Gnosis Safe/Proxy?
        # Assuming simple EOA for now as per trader config directly using key.
        
        # Determine unique index sets to redeem
        index_sets = []
        for p in pos_list:
            outcome_idx_str = p.get('outcomeIndex')
            if outcome_idx_str is not None:
                idx_val = int(outcome_idx_str)
                # Index Set is a bitmask: 1 << index
                idx_set = 1 << idx_val
                if idx_set not in index_sets:
                    index_sets.append(idx_set)
        
        if not index_sets:
            return

        console.print(f"[bold yellow]🔄 Redeeming for condition {condition_id[:10]}... (IndexSets: {index_sets})[/bold yellow]")
        
        try:
            parent_collection_id = b'\x00' * 32
            
            # Web3.py sometimes needs bytes/hex formatting carefully
            # condition_id is likely hex string "0x..." 
            
            # Build TX
            tx_func = ctf.functions.redeemPositions(
                USDC_ADDRESS,
                parent_collection_id,
                condition_id,
                index_sets
            )
            
            estimated_gas = tx_func.estimate_gas({'from': wallet_address})
            
            tx = tx_func.build_transaction({
                'from': wallet_address,
                'nonce': self.w3.eth.get_transaction_count(wallet_address),
                'gas': int(estimated_gas * 1.2), # Buffer
                'gasPrice': self.w3.eth.gas_price,
                'chainId': 137
            })
            
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=Config.PRIVATE_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            
            console.print(f"[green]✔ Redemption TX sent: {self.w3.to_hex(tx_hash)}[/green]")
            
            # Simple wait (blocking async loop? Use executor if heavy wait)
            # Actually wait_for_transaction_receipt is blocking. 
            # We should probably return and let user check explorer, or await in thread.
            # But for simplicity, we await here.
            # Convert to awaitable if using async web3, but we are using sync web3 here.
            # To avoid blocking loop:
            
            loop = asyncio.get_running_loop()
            receipt = await loop.run_in_executor(None, self.w3.eth.wait_for_transaction_receipt, tx_hash)
            
            if receipt.status == 1:
                console.print(f"[bold green]✔ Redemption Confirmed for {condition_id[:10]}![/bold green]")
            else:
                 console.print(f"[bold red]✘ Redemption Failed for {condition_id[:10]}[/bold red]")

        except Exception as e:
            console.print(f"[red]Redemption Error: {e}[/red]")
