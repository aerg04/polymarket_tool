import asyncio
import aiohttp
import time
from datetime import datetime
from src.config import Config, console
from rich.panel import Panel

class Tracker:
    def __init__(self, process_transaction_callback):
        self.targets = [t.lower() for t in Config.TARGET_WALLETS]
        self.base_url = "https://data-api.polymarket.com/activity"
        self.callback = process_transaction_callback
        self.seen_activity_ids = set()
        self.first_run = True
        self.poll_interval = 3.0

    async def fetch_activity(self, session, wallet):
        """Consulta la API de Polymarket para una wallet específica."""
        params = {
            "user": wallet,
            "limit": "10",
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC"
        }
        try:
            async with session.get(self.base_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return wallet, data
                elif response.status == 429:
                    console.print("[yellow]⚠️ Rate Limit (429). Pausando...[/yellow]")
                    await asyncio.sleep(2)
                    return wallet, []
                else:
                    console.print(f"[red]Error {response.status} checking {wallet[:6]}...[/red]")
                    return wallet, []
        except Exception as e:
            console.print(f"[red]Error de conexión: {str(e)}[/red]")
            return wallet, []

    def process_activity(self, wallet, activities):
        """Filtra y procesa las actividades nuevas."""
        # Procesamos desde el más antiguo al más nuevo
        new_activities = []
        for act in reversed(activities):
            # Usamos conditionId y side para evitar spam de ordenes large que se llenan parcialmente
            # La API puede devolver multiples eventos "TRADE" para una sola orden limite
            # Agrupamos por Market (conditionId) + Lado (BUY/SELL)
            condition_id = act.get('conditionId')
            side = act.get('side')
            act_id = f"{condition_id}_{side}"
            
            if act_id in self.seen_activity_ids:
                continue
            
            self.seen_activity_ids.add(act_id)
            
            if self.first_run:
                continue

            # FILTRO: Solo nos interesan Trades
            if act.get('type') == "TRADE":
                # Inject wallet into activity object for callback context
                act['wallet_address'] = wallet
                new_activities.append(act)

        return new_activities

    async def start_monitoring(self):
        console.print("[bold green]🚀 Iniciando Polymarket Tracker (API Mode)[/bold green]")
        console.print(f"[cyan]📡 Monitoreando {len(self.targets)} wallets...[/cyan]")
        
        async with aiohttp.ClientSession() as session:
            while True:
                tasks = [self.fetch_activity(session, w) for w in self.targets]
                
                results = await asyncio.gather(*tasks)
                
                for wallet, activities in results:
                    #print(f"[blue]🔍 Revisando actividad para {wallet[:6]}...[/blue]")
                   
                    if activities:
                        new_acts = self.process_activity(wallet, activities)
                        for act in new_acts:
                             # Call the callback with the activity object
                             print(f"[blue]🔔 Nueva actividad detectada para {wallet[:6]}...[/blue]")
                             await self.callback(act)
                
                if self.first_run:
                    console.print("[blue]ℹ️  Historial inicial cargado. Esperando nuevos movimientos...[/blue]")
                    self.first_run = False
                
                await asyncio.sleep(self.poll_interval)
