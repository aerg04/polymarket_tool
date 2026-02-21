import aiohttp
import json
from rich.console import Console
from src.config import Config

console = Console()
# Use CLOB API for precise market lookup by condition_id

class MarketAPI:
    @staticmethod
    async def get_token_ids(condition_id):
        """
        Fetches the clobTokenIds (YES/NO token IDs) for a given condition_id from CLOB API.
        Returns (yes_token_id, no_token_id) or (None, None) if not found/error.
        """
        if not condition_id:
            return None, None
            
        url = f"{Config.POLYMARKET_CLOB_API_URL}/markets/{condition_id}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Data should be a single market object
                        # Structure: "tokens": [{"token_id": "...", "outcome": "Yes"}, ...]
                        tokens = data.get('tokens')
                        if tokens and isinstance(tokens, list):
                            yes_id = None
                            no_id = None
                            
                            for t in tokens:
                                if t.get('outcome') == "Yes":
                                    yes_id = t.get('token_id')
                                elif t.get('outcome') == "No":
                                    no_id = t.get('token_id')
                            
                            # If we found at least one, return them (some markets might be weird)
                            # But ideally we want both
                            if yes_id or no_id:
                                return yes_id, no_id
                                
                    elif response.status == 404:
                         console.print(f"[yellow]Market not found in CLOB for {condition_id}[/yellow]")
                    else:
                        console.print(f"[red]Error fetching CLOB market: {response.status}[/red]")

        except Exception as e:
            console.print(f"[red]Error fetching market details: {e}[/red]")
            
        return None, None
