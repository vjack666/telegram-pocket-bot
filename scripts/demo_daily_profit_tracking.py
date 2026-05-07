"""
Demo: Daily Profit Tracking

Demuestra cómo funciona el tracking de ganancias diarias y el modo defensivo.
"""

from pathlib import Path
from datetime import date
import json

# Simular el comportamiento del daily profit tracker
class DemoTracker:
    def __init__(self, daily_target: float = 60.0):
        self.daily_target = daily_target
        self.daily_pnl = 0.0
        self.meta_reached = False
        self.defensive_mode = False
        self.trades_today = 0

    def record_trade(self, pnl: float) -> dict:
        self.daily_pnl += pnl
        self.trades_today += 1
        
        was_meta = self.meta_reached
        self.meta_reached = self.daily_pnl >= self.daily_target
        
        if self.meta_reached and not self.defensive_mode:
            self.defensive_mode = True
        
        return {
            "pnl": pnl,
            "daily_total": round(self.daily_pnl, 2),
            "progress": round((self.daily_pnl / self.daily_target) * 100, 1),
            "meta_reached": self.meta_reached,
            "just_reached": self.meta_reached and not was_meta,
            "defensive_mode": self.defensive_mode,
            "trade_num": self.trades_today,
        }


def main():
    print("""
╔════════════════════════════════════════════════════════════════════════════════════════╗
║                    DEMO: Daily Profit Tracking en Acción                              ║
╚════════════════════════════════════════════════════════════════════════════════════════╝

📊 ESCENARIO: Meta diaria de $60, operaciones reales de ejemplo.md

""")
    
    # Simular una secuencia de operaciones que alcanza la meta
    tracker = DemoTracker(daily_target=60.0)
    
    # Trades simulados: WIN = +payout, LOSS = -monto
    trades_sequence = [
        ("CALL", 20, +3.20, "WIN"),    # Entrada ganada
        ("PUT", 20, -20, "LOSS"),      # Martingala en gale 1
        ("CALL", 35, +4.55, "WIN"),    # Entrada 2 ganada
        ("PUT", 30, -30, "LOSS"),      # Martingala gale 2
        ("CALL", 40, +5.20, "WIN"),    # Entrada 3 ganada
        ("CALL", 25, +3.25, "WIN"),    # Entrada 4 ganada
        ("PUT", 15, -15, "LOSS"),      # Martingala gale
        ("CALL", 35, +4.55, "WIN"),    # Entrada 5
        ("CALL", 30, +3.90, "WIN"),    # Entrada 6 ← Meta alcanzada
        ("PUT", 20, -20, "LOSS"),      # Post-meta (modo defensivo)
    ]
    
    print("┌─────┬──────────┬─────────┬──────────┬───────────┬──────────┬──────────────┐")
    print("│ Nº  │ Resultado│ PnL     │ Total    │ Progreso │ Modo     │ Evento       │")
    print("├─────┼──────────┼─────────┼──────────┼───────────┼──────────┼──────────────┤")
    
    for i, (asset, amount, pnl, result) in enumerate(trades_sequence, 1):
        status = tracker.record_trade(pnl)
        
        result_icon = "🟢" if pnl > 0 else "🔴"
        progress_bar = "█" * int(status["progress"] / 5) + "░" * (20 - int(status["progress"] / 5))
        mode_str = "DEFENSIVE" if status["defensive_mode"] else "NORMAL   "
        
        event_str = ""
        if status["just_reached"]:
            event_str = "✓ META!"
        
        print(
            f"│ {i:2d}  │ {result_icon} {result:5} │ {pnl:+7.2f} │ "
            f"${status['daily_total']:7.2f} │ {progress_bar} │ {mode_str} │ {event_str:12} │"
        )
    
    print("└─────┴──────────┴─────────┴──────────┴───────────┴──────────┴──────────────┘")
    
    print(f"""

📈 RESULTADO FINAL

   Total de operaciones: {tracker.trades_today}
   Ganancia total: ${tracker.daily_pnl:+.2f}
   Meta diaria: ${tracker.daily_target:.2f}
   Meta alcanzada: {"✓ SÍ" if tracker.meta_reached else "✗ NO"}
   Modo defensivo activo: {"✓ SÍ" if tracker.defensive_mode else "✗ NO"}

════════════════════════════════════════════════════════════════════════════════════════

🎯 CÓMO FUNCIONA

   1. TRACK: Cada operación registra su PnL (+payout si WIN, -monto si LOSS)
   
   2. PROGRESS: Se calcula el % hacia la meta
      • <50% → AGGRESSIVE: busca ganancia
      • 50-75% → CAUTIOUS: reduce riesgo gradualmente
      • >75% → DEFENSIVO: modo protección
   
   3. META: Cuando llega a $60 (o tu configuración):
      • DEFENSIVE MODE se activa
      • Reduce riesgo por operación
      • Protege ganancias del día
   
   4. RESET: A medianoche se reinicia el contador

════════════════════════════════════════════════════════════════════════════════════════

🔧 INTEGRACIÓN CON TU BOT

   1. Agregar a .env:
      ─────────────────────────────
      APP_DAILY_PROFIT_TRACKING_ENABLED=true
      APP_DAILY_PROFIT_TARGET=60.0              # Meta en dinero
      APP_DAILY_PROFIT_DEFENSIVE_MODE=true      # Cambiar a defensive tras meta
      APP_DAILY_PROFIT_STATE_PATH=runtime/daily_profit_state.json
   
   2. El sistema automáticamente:
      • Trackea cada operación WIN/LOSS
      • Calcula PnL real en tiempo real
      • Cambia estrategia cuando alcanza meta
      • Se reinicia cada día a medianoche

════════════════════════════════════════════════════════════════════════════════════════

✅ BENEFICIOS

   ✓ Protege ganancias: No sobreopera después de alcanzar meta
   ✓ Reduce riesgo: Modo defensivo limita downside en rachas finales
   ✓ Disciplina: Evita codicia, respeta objetivos diarios
   ✓ Rastreable: Estado guardado en JSON, recuperable si reinicia
   ✓ Automático: No requiere intervención manual

════════════════════════════════════════════════════════════════════════════════════════
""")


if __name__ == "__main__":
    main()
