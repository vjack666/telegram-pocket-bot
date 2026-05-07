#!/usr/bin/env python3
"""
Simulación de trading usando histórico de ejemplo.md
"""

# Datos del histórico de ejemplo.md
trades = [
    # Sesión 1
    ("AUD/USD", "direct_win", 0),      # VICTORIA DIRECTA
    ("EUR/USD", "gale1_win", 1),       # VICTORIA EN G1
    ("USD/JPY", "direct_win", 0),      # VICTORIA DIRECTA
    ("GBP/USD", "loss", 0),            # PÉRDIDA
    ("AUD/NZD", "gale1_win", 1),       # VICTORIA EN G1
    ("USD/CAD", "gale1_win", 1),       # VICTORIA EN G1
    # Sesión 2
    ("EUR/USD", "gale2_win", 2),       # VICTORIA EN G2
    ("AUD/USD", "direct_win", 0),      # VICTORIA DIRECTA
    ("GBP/USD", "gale2_win", 2),       # VICTORIA EN G2
    ("NZD/USD", "loss", 0),            # PÉRDIDA
    ("USD/CAD", "direct_win", 0),      # VICTORIA DIRECTA
    ("USD/CHF", "direct_win", 0),      # VICTORIA DIRECTA
]

PAYOUT = 0.92  # 92%
STAKES = [2.0, 4.0, 10.0]  # Entrada, G1, G2
INITIAL_BALANCE = 100.0

def simulate():
    balance = INITIAL_BALANCE
    total_invested = 0
    total_wins = 0
    total_losses = 0
    
    print("=" * 90)
    print(f"SIMULACIÓN CON EJEMPLO.MD | Balance Inicial: ${INITIAL_BALANCE:.2f} | Payout: {PAYOUT*100:.0f}%")
    print("=" * 90)
    print(f"{'Op':<3} {'Asset':<12} {'Resultado':<15} {'Step':<6} {'Inversión':<10} {'Ganancia':<10} {'Balance':<10}")
    print("-" * 90)
    
    for i, (asset, result_type, gale_step) in enumerate(trades, 1):
        stake = STAKES[gale_step]
        
        if result_type == "loss":
            # Pérdida: se pierde toda la apuesta
            loss = stake
            balance -= loss
            total_invested += stake
            total_losses += 1
            result_str = "❌ PÉRDIDA"
            gain_str = f"-${loss:.2f}"
        else:
            # Victoria: payout * stake
            gain = stake * PAYOUT
            balance += gain
            total_invested += stake
            total_wins += 1
            if result_type == "direct_win":
                result_str = "✅ DIRECTA"
            elif result_type == "gale1_win":
                result_str = "✅ GALE1"
            else:  # gale2_win
                result_str = "✅ GALE2"
            gain_str = f"+${gain:.2f}"
        
        gale_label = f"G{gale_step}" if gale_step > 0 else "E"
        print(f"{i:<3} {asset:<12} {result_str:<15} {gale_label:<6} ${stake:<9.2f} {gain_str:<10} ${balance:.2f}")
    
    print("-" * 90)
    print(f"\n📊 RESUMEN FINAL")
    print(f"   Victorias: {total_wins}/{len(trades)} ({total_wins/len(trades)*100:.1f}%)")
    print(f"   Pérdidas:  {total_losses}/{len(trades)}")
    print(f"   Total Invertido: ${total_invested:.2f}")
    print(f"   Balance Inicial:  ${INITIAL_BALANCE:.2f}")
    print(f"   Balance Final:    ${balance:.2f}")
    print(f"   GANANCIA NETA:    ${balance - INITIAL_BALANCE:+.2f} ({(balance/INITIAL_BALANCE - 1)*100:+.1f}%)")
    print(f"\n   ¿Meta $60? {'✅ SÍ!' if balance >= 60 else '❌ No'} (Necesitaba ${60 - INITIAL_BALANCE:.2f})")
    print("=" * 90)

if __name__ == "__main__":
    simulate()
