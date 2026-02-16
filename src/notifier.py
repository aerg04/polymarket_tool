import aiohttp
from src.config import Config, console

class Notifier:
    def __init__(self):
        self.token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage"

    async def send_alert(self, message: str):
        """Sends a message to the configured Telegram chat."""
        if not self.token or not self.chat_id:
            console.print("[yellow]Telegram config missing. Skipping alert.[/yellow]")
            return

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.base_url, json=payload) as response:
                    if response.status == 200:
                        console.print(f"[green]Telegram alert sent: {message[:50]}...[/green]")
                    else:
                        err_text = await response.text()
                        console.print(f"[bold red]Failed to send Telegram alert: {err_text}[/bold red]")
        except Exception as e:
            console.print(f"[bold red]Error sending Telegram alert: {e}[/bold red]")
