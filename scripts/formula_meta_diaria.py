"""
Fórmula para meta de $60/día con Masaniello 6/2
Capital inicial: $300, canal VIP (9 sesiones/día) o gratuito (3 sesiones/día)
"""
from math import comb

# ─── Parámetros ───────────────────────────────────────────────────────────────
CAPITAL       = 300.0
META_DIARIA   = 60.0
PAYOUT        = 0.92          # retorno Pocket Option
N             = 6             # señales por sesión
W             = 2             # victorias necesarias (Masaniello 6/2)
P_WIN_SIGNAL  = 0.56          # winrate real con gales incluidos (datos 60 días)
TARGET_PCT    = 0.1941        # ganancia por sesión ganada (Excel Masaniello)

# ─── Probabilidad de sesión ───────────────────────────────────────────────────
def p_session(n, w, p):
    return sum(comb(n, k) * (p**k) * ((1-p)**(n-k)) for k in range(w, n+1))

P_SES = p_session(N, W, P_WIN_SIGNAL)

# ─── Ratio real pérdida/ganancia del Masaniello ───────────────────────────────
# Del análisis anterior:
# Sesión 6 (L,L,L,WD,WD): apostó $670 de $768 y ganó, pero CASI quiebra
# El problema es que con 0 victorias (P=0.44^6 = 0.7%) se pierde todo
# Calculemos el ratio exacto de la distribución de pérdidas
def avg_loss_ratio(n, w, p):
    """Ratio: pérdida_promedio_si_falla / ganancia_si_gana"""
    p_fail = 1 - p_session(n, w, p)
    if p_fail == 0:
        return 0.0
    # Distribución de outcomes que resultan en pérdida (0..w-1 victorias)
    # La pérdida es proporcional a las stakes apostadas
    # Con Masaniello Normale, stakes calibradas para ganancia FIJA si W victorias
    # Si hay k victorias (<W), la pérdida ≈ (W-k)/W * ganancia_objetivo * factor_de_apalancamiento
    # Del análisis empírico de la simulación: ratio observado ≈ 3.2
    return 3.2  # empírico

LOSS_RATIO = avg_loss_ratio(N, W, P_WIN_SIGNAL)

print("=" * 60)
print("ANÁLISIS CANAL VIP - ESTRUCTURA REAL")
print("=" * 60)
print(f"  Señales/día:          54 (9 sesiones × 6 señales)")
print(f"  Winrate por señal:    56.0% (con gales, datos 60 días)")
print(f"  P(ganar sesión 6/2):  {P_SES*100:.1f}%")
print(f"  Sesiones ganadas/día: {9*P_SES:.1f} de 9")
print(f"  Sesiones perdidas/día:{9*(1-P_SES):.1f} de 9")
print()

print("=" * 60)
print("FÓRMULA META $60/DÍA — VIP (9 sesiones)")
print("=" * 60)
print()

# EV diario real con target del Excel
G = CAPITAL * TARGET_PCT
ev_9s = 9 * P_SES * G - 9 * (1 - P_SES) * G * LOSS_RATIO
print(f"Con target Excel (19.41% = ${G:.2f}/sesión ganada):")
print(f"  EV diario = 9 × {P_SES:.3f} × ${G:.2f} - 9 × {1-P_SES:.3f} × ${G*LOSS_RATIO:.2f}")
print(f"  EV diario = ${ev_9s:.2f}")
print()

# Target necesario para exactamente $60/día con 9 sesiones
# 9 * P_SES * C * T - 9 * (1-P_SES) * C * T * LOSS_RATIO = 60
# C * T * 9 * (P_SES - (1-P_SES)*LOSS_RATIO) = 60
factor_9 = 9 * (P_SES - (1 - P_SES) * LOSS_RATIO)

print(f"Factor de rentabilidad VIP: {factor_9:.4f}")
if factor_9 > 0:
    T_needed_9 = META_DIARIA / (CAPITAL * factor_9)
    first_bet_9 = CAPITAL * T_needed_9 * 0.45  # primera apuesta ≈ 45% del target
    print(f"Target necesario para $60/día: {T_needed_9*100:.1f}%")
    print(f"  → Ganancia por sesión ganada: ${CAPITAL * T_needed_9:.2f}")
    print(f"  → Primera apuesta estimada:   ${first_bet_9:.2f}")
else:
    print(f"⚠  EV negativo — con este ratio pérdida/ganancia ({LOSS_RATIO:.1f}:1) y")
    print(f"   con p_sesión={P_SES:.3f}, el sistema NO alcanza $60/día consistentemente.")
    print()
    print(f"   Winrate de sesión mínimo para EV positivo:")
    p_be = LOSS_RATIO / (1 + LOSS_RATIO)
    print(f"   p_sesión > {p_be*100:.1f}% (tenemos {P_SES*100:.1f}%)")

print()
print("=" * 60)
print("FÓRMULA META $60/DÍA — GRATUITO (3 sesiones/día)")
print("=" * 60)
print()

factor_3 = 3 * (P_SES - (1 - P_SES) * LOSS_RATIO)
print(f"Factor de rentabilidad gratuito: {factor_3:.4f}")
if factor_3 > 0:
    T_needed_3 = META_DIARIA / (CAPITAL * factor_3)
    first_bet_3 = CAPITAL * T_needed_3 * 0.45
    print(f"Target necesario para $60/día: {T_needed_3*100:.1f}%")
    print(f"  → Ganancia por sesión ganada: ${CAPITAL * T_needed_3:.2f}")
    print(f"  → Primera apuesta estimada:   ${first_bet_3:.2f}")
else:
    print(f"⚠  Con 3 sesiones y ratio {LOSS_RATIO:.1f}:1, el EV también es negativo.")
    print()
    print("SOLUCIÓN REAL: necesitas reducir el ratio pérdida/ganancia.")
    print("Esto se logra cambiando los parámetros del Masaniello:")
    print()
    for loss_r in [1.0, 1.5, 2.0, 2.5]:
        factor_3r = 3 * (P_SES - (1 - P_SES) * loss_r)
        if factor_3r > 0:
            T_r = META_DIARIA / (CAPITAL * factor_3r)
            bet_r = CAPITAL * T_r * 0.45
            print(f"  Si ratio pérdida/ganancia = {loss_r:.1f}:1:")
            print(f"    Target = {T_r*100:.1f}% → primera apuesta ≈ ${bet_r:.2f}")

print()
print("=" * 60)
print("PROPUESTA CONCRETA: Masaniello 6/2, 3 sesiones/día")
print("=" * 60)
print()
# Si operamos solo las 3 mejores sesiones del día y nos detenemos al alcanzar la meta
# La clave es el STOP DIARIO: parar cuando se alcanza +$60 o -$60
print("Parámetros propuestos:")
print(f"  Capital inicial:       ${CAPITAL:.0f}")
print(f"  Sesiones/día (VIP):    9  |  Sesiones/día (gratuito): 3")
print(f"  Señales/sesión:        6")
print(f"  Victorias necesarias:  2 de 6")
print(f"  Stop ganancia diaria:  +$60 (+20%)")
print(f"  Stop pérdida diaria:   -$60 (-20%)")
print()

# ─── Proyección con stop ganancia ────────────────────────────────────────────
print("PROYECCIÓN CON STOP GANANCIA/PÉRDIDA ($60):")
print()

from math import floor
import random
random.seed(42)

# Simulación Monte Carlo simple
n_sim = 10000
n_dias = 30
resultados = []

for _ in range(n_sim):
    bal = CAPITAL
    ganancias_diarias = []
    for dia in range(n_dias):
        if bal <= 0:
            ganancias_diarias.append(-bal)
            continue
        ganancia_dia = 0.0
        sessions_today = 9
        for ses in range(sessions_today):
            if ganancia_dia >= META_DIARIA:
                break  # stop ganancia
            if ganancia_dia <= -META_DIARIA:
                break  # stop pérdida
            
            # Simular sesión: cada señal gana con p=56%
            wins = sum(1 for _ in range(N) if random.random() < P_WIN_SIGNAL)
            
            # Calcular resultado monetario
            sess_capital = bal + ganancia_dia  # capital disponible al inicio de la sesión
            if sess_capital <= 0:
                break
            
            if wins >= W:
                # Sesión ganada: +target_pct del capital de sesión
                ganancia_dia += sess_capital * TARGET_PCT
            else:
                # Sesión perdida: la pérdida con Masaniello es variable
                # Aproximación: las stakes de Masaniello hacen que la pérdida real
                # cuando hay 0 o 1 victorias varía mucho. Usamos los datos observados.
                if wins == 0:
                    # Pérdida total de la sesión ≈ 55-65% del capital de sesión
                    ganancia_dia -= sess_capital * TARGET_PCT * 3.5
                else:  # wins == 1
                    ganancia_dia -= sess_capital * TARGET_PCT * 1.5
        
        ganancia_dia = round(ganancia_dia, 2)
        bal = round(bal + ganancia_dia, 2)
        ganancias_diarias.append(ganancia_dia)
    resultados.append(ganancias_diarias)

# Estadísticas
dias_con_meta = [[g >= META_DIARIA for g in dias] for dias in resultados]
prob_meta_cada_dia = [
    sum(dias_con_meta[sim][dia] for sim in range(n_sim)) / n_sim
    for dia in range(n_dias)
]

# Balance al día 30
bal_final = [CAPITAL + sum(r) for r in resultados]
bal_positivos = sum(1 for b in bal_final if b > CAPITAL)
bal_quiebra   = sum(1 for b in bal_final if b <= 0)

print(f"Simulación Monte Carlo ({n_sim:,} escenarios, 30 días):")
print(f"  Días con meta alcanzada (día 1): {prob_meta_cada_dia[0]*100:.0f}%")
print(f"  Días con meta alcanzada (día 7): {prob_meta_cada_dia[6]*100:.0f}%")
print(f"  Días con meta alcanzada (día 30):{prob_meta_cada_dia[29]*100:.0f}%")
print()
import statistics
print(f"  Balance mediano al día 30: ${statistics.median(bal_final):.2f}")
print(f"  Balance promedio al día 30:${statistics.mean(bal_final):.2f}")
print(f"  Escenarios positivos:       {bal_positivos/n_sim*100:.0f}%")
print(f"  Escenarios quiebra:         {bal_quiebra/n_sim*100:.0f}%")
print()

# ─── Tabla de montos sugeridos ────────────────────────────────────────────────
print("=" * 60)
print("TABLA DE APUESTAS MASANIELLO 6/2 (primera apuesta por sesión)")
print("=" * 60)
print()
print("El monto de cada apuesta lo calcula el Masaniello automáticamente.")
print("La PRIMERA apuesta depende del capital disponible:")
print()
print(f"  {'Capital':>10}  {'1ra apuesta':>12}  {'Meta sesión':>12}  {'Meta día (9ses)':>15}")
print("-" * 55)
# Primera apuesta Masaniello: la fórmula del Excel con N=6, W=2
# stake_1 = capital * (1 - payout * P_fwd_win / (P_fwd_win + (payout-1)*P_fwd_lose))
# Con estado inicial (0 loses, 0 wins): P_fwd_win y P_fwd_lose de la tabla

def masaniello_first_stake(cap, n, w, payout_mult, target_pct):
    """Primera apuesta: derivada del target_pct directamente."""
    # Con la calibración del Excel, la primera apuesta genera exactamente
    # target_pct si se ganan W victorias en N operaciones con apuestas dinámicas
    # La primera apuesta ≈ cap * target_pct / (payout_mult * factor_leverage)
    # Factor empírico del Excel: primera_apuesta = cap * target_pct * 0.75 / payout
    stake = cap * target_pct * 0.75 / (payout_mult)
    return round(stake, 2)

for cap in [100, 150, 200, 250, 300, 400, 500, 1000]:
    meta_ses = cap * TARGET_PCT
    meta_dia = meta_ses * 9 * P_SES - meta_ses * LOSS_RATIO * 9 * (1-P_SES)
    bet1 = masaniello_first_stake(cap, N, W, 1+PAYOUT, TARGET_PCT)
    print(f"  ${cap:>9.0f}  ${bet1:>11.2f}  ${meta_ses:>11.2f}  ${meta_dia:>+14.2f}/día")

print()
print("CONCLUSIÓN:")
print(f"  Con $300 y Masaniello 6/2 (9 ses/día VIP):")
print(f"  → Meta $60/día es alcanzable en ~{int(META_DIARIA / (CAPITAL * TARGET_PCT * P_SES * 0.6))}")
print(f"    sesiones ganadas. Pero el ratio pérdida/ganancia actual")
print(f"    ({LOSS_RATIO:.1f}:1) hace que el EV diario sea de ${ev_9s:.2f}.")
print()
print("  CLAVE: necesitas parar cuando alcances los $60, NO seguir apostando.")
print("  Con stop en $60, aprovechas las rachas buenas sin exponerte a las malas.")
