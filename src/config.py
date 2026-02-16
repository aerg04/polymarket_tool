import os
from dotenv import load_dotenv
from rich.console import Console

# Load environment variables
load_dotenv()

console = Console()

class Config:
    # Node
    POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL")
    
    # Wallet
    PRIVATE_KEY = os.getenv("PRIVATE_KEY")
    MY_WALLET_ADDRESS = os.getenv("MY_WALLET_ADDRESS")
    
    # Target
    # Split comma-separated string into a list
    TARGET_WALLETS = [w.strip() for w in os.getenv("TARGET_WALLETS", "").split(",") if w.strip()]
    
    # Polymarket API Endpoints
    POLYMARKET_CLOB_API_URL = "https://clob.polymarket.com" # For placing orders
    POLYMARKET_GAMMA_API_URL = "https://gamma-api.polymarket.com" # For looking up market names
    
    # CONTRACTS
    # The Gnosis Conditional Tokens Framework (CTF) Contract
    # This is the contract that actually emits the TransferSingle/TransferBatch events for all Polymarket positions.
    POLYMARKET_CTF_CONTRACT = "0x4D97DCd979c96e26e7e5B98850a82448F20f68e5"
    
    # The CTF Exchange (Proxy) - Used for verifying if the trade went through the exchange (optional secondary check)
    POLYMARKET_EXCHANGE_CONTRACT = os.getenv("POLYMARKET_EXCHANGE_CONTRACT", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6De8B79665")
    
    # Trading
    BET_MODE = os.getenv("BET_MODE", "FIXED").upper() # FIXED or PERCENTAGE
    
    try:
        BET_AMOUNT_USDC = float(os.getenv("BET_AMOUNT_USDC", "10"))
    except ValueError:
        BET_AMOUNT_USDC = 10.0
        
    try:
        BET_PERCENTAGE = float(os.getenv("BET_PERCENTAGE", "0.05"))
    except ValueError:
        BET_PERCENTAGE = 0.05
        
    SLIPPAGE_TOLERANCE = float(os.getenv("SLIPPAGE_TOLERANCE", "0.01"))
    
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    @classmethod
    def validate(cls):
        """Checks if essential config variables are present."""
        missing = []
        if not cls.POLYGON_RPC_URL: missing.append("POLYGON_RPC_URL")
        if not cls.TARGET_WALLETS: missing.append("TARGET_WALLETS")
        if not cls.PRIVATE_KEY: missing.append("PRIVATE_KEY")
        
        if missing:
            console.print(f"[bold red]CRITICAL ERROR: Missing environment variables: {', '.join(missing)}[/bold red]")
            return False
            
        # Normalize addresses to checksum
        try:
            from web3 import Web3
            cls.TARGET_WALLETS = [Web3.to_checksum_address(w) for w in cls.TARGET_WALLETS]
            
            if cls.POLYMARKET_EXCHANGE_CONTRACT:
                cls.POLYMARKET_EXCHANGE_CONTRACT = Web3.to_checksum_address(cls.POLYMARKET_EXCHANGE_CONTRACT)
            
            # Helper to checksum the new CTF contract
            if cls.POLYMARKET_CTF_CONTRACT:
                cls.POLYMARKET_CTF_CONTRACT = Web3.to_checksum_address(cls.POLYMARKET_CTF_CONTRACT)
                
            if cls.MY_WALLET_ADDRESS:
                cls.MY_WALLET_ADDRESS = Web3.to_checksum_address(cls.MY_WALLET_ADDRESS)
        except Exception as e:
            console.print(f"[bold red]Address validation error: {e}[/bold red]")
            return False
            
        return True
