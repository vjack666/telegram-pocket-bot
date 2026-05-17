from src.core.gale_calculator import GaleCalculator

# Estado inicial
calc = GaleCalculator(
    saldo_actual=102.0,
    payout=0.92,
    objetivo_manual=None,
    objetivo_entero_par=True
)

print("=== CICLO DE OPERACIÓN ===\n")

# ENTRY
calc.recalcular_inversion()
entry = calc.inversion_actual
print(f"ENTRY:")
print(f"  Saldo: ${calc.saldo_actual:.2f}")
print(f"  Objetivo: ${calc.saldo_objetivo:.0f}")
print(f"  Stake: ${entry:.2f}\n")

# Simular pérdida 1
calc.on_perdio()
g1 = calc.inversion_actual
print(f"G1 (después pérdida 1):")
print(f"  Saldo: ${calc.saldo_actual:.2f}")
print(f"  Objetivo: ${calc.saldo_objetivo:.0f} (FIJO)")
print(f"  Stake: ${g1:.2f}\n")

# Simular pérdida 2
calc.on_perdio()
g2 = calc.inversion_actual
print(f"G2 (después pérdida 2):")
print(f"  Saldo: ${calc.saldo_actual:.2f}")
print(f"  Objetivo: ${calc.saldo_objetivo:.0f} (FIJO)")
print(f"  Stake: ${g2:.2f}\n")

print(f"SECUENCIA: ${entry:.2f} → ${g1:.2f} → ${g2:.2f}")
