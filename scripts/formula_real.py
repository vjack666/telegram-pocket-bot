"""
Fórmula REAL para meta $60/dia con $300 - Masaniello 6/2
Usa el ratio real observado en la simulacion con datos historicos del canal.
"""
import math
from math import comb

# Parametros
CAPITAL       = 300.0
META          = 60.0
PAYOUT        = 0.92
N             = 6
W             = 2
P_WIN_SIGNAL  = 0.56
TARGET_PCT    = 0.1941  # del Excel original
REAL_LOSS_RATIO = 4.5   # ratio real observado en simulacion (session 6, 8, 12, 17)
N_VIP         = 9
N_FREE        = 3

def p_session(n, w, p):
    return sum(comb(n, k) * (p**k) * ((1-p)**(n-k)) for k in range(w, n+1))

P_SES = p_session(N, W, P_WIN_SIGNAL)

print("=" * 58)
print("  ESTRUCTURA REAL DEL CANAL VIP")
print("=" * 58)
print(f"  Sesiones VIP/dia:    {N_VIP}  (franjas horarias fijas)")
print(f"  Sesiones free/dia:   {N_FREE}")
print(f"  Senales/sesion:      {N} (1 entrada + 2 gales)")
print(f"  Winrate por senal:   {P_WIN_SIGNAL*100:.0f}% (con gales incluidos)")
print(f"  P(ganar sesion 6/2): {P_SES*100:.1f}%")
print(f"  Ratio perdida/ganancia real: {REAL_LOSS_RATIO:.1f}:1")
print()

# EV real por sesion
G = CAPITAL * TARGET_PCT
ev_ses = P_SES * G - (1 - P_SES) * G * REAL_LOSS_RATIO
ev_vip  = N_VIP  * ev_ses
ev_free = N_FREE * ev_ses

print("=" * 58)
print("  EV REAL CON TARGET EXCEL (19.41% = $58/sesion)")
print("=" * 58)
print(f"  Ganancia si gana sesion:  +${G:.2f}")
print(f"  Perdida si pierde sesion: -${G * REAL_LOSS_RATIO:.2f}")
print(f"  EV por sesion:             ${ev_ses:.2f}")
print(f"  EV diario VIP  (9 ses):    ${ev_vip:.2f}")
print(f"  EV diario free (3 ses):    ${ev_free:.2f}")
print()

var_ses = P_SES * (G**2) + (1 - P_SES) * ((G * REAL_LOSS_RATIO)**2) - ev_ses**2
std_ses = math.sqrt(var_ses)
print(f"  Std. dev. por sesion:      ${std_ses:.2f}")
print(f"  Std. dev. por dia (9 ses): ${std_ses * math.sqrt(9):.2f}")
print()

print("  PELIGRO: Si llegas a 5 wins seguidos (balance $849)")
peak = CAPITAL * (1 + TARGET_PCT) ** 5
loss_peak = peak * TARGET_PCT * REAL_LOSS_RATIO
print(f"  y pierdes UNA sesion: pierdes ${loss_peak:.0f}")
print(f"  Balance queda en:              ${peak - loss_peak:.0f}  (vs $849 de pico)")
print()

print("=" * 58)
print("  ALTERNATIVAS DE CONFIGURACION")
print("=" * 58)
print()
headers = f"  {'Target%':>8}  {'Ganancia':>10}  {'Perdida':>10}  {'EV/dia VIP':>12}  {'Ses p/meta':>10}"
print(headers)
print("  " + "-" * 56)
for t_pct in [0.05, 0.10, 0.15, 0.1941, 0.25, 0.30]:
    g = CAPITAL * t_pct
    ev = P_SES * g - (1 - P_SES) * g * REAL_LOSS_RATIO
    ev_d = ev * N_VIP
    ses_needed = META / g if g > 0 else 999
    star = " <--Excel" if abs(t_pct - 0.1941) < 0.001 else ""
    print(f"  {t_pct*100:>7.1f}%  ${g:>9.2f}  ${g*REAL_LOSS_RATIO:>9.2f}  ${ev_d:>+11.2f}  {ses_needed:>9.1f}{star}")

print()
print("=" * 58)
print("  SOLUCION RECOMENDADA: TARGET FIJO EN DOLARES")
print("=" * 58)
print()
print("  Problema del target porcentual: escala con el balance.")
print("  Si el balance crece, la perdida potencial crece igual.")
print("  SOLUCION: fijar un monto fijo de ganancia por sesion.")
print()

# Target fijo para ganar exactamente lo necesario
fixed_win = 20.0  # ganar $20/sesion ganada
stake1 = fixed_win / (PAYOUT * 2)  # aprox primera apuesta
loss_fixed = fixed_win * REAL_LOSS_RATIO
ev_fixed = P_SES * fixed_win - (1 - P_SES) * loss_fixed
print(f"  Configuracion: ganar ${fixed_win:.0f} fijos por sesion ganada")
print(f"  Primera apuesta por sesion:    ${stake1:.2f}")
print(f"  Perdida si falla la sesion:    ${loss_fixed:.2f}  (estimada)")
print(f"  EV por sesion:                 ${ev_fixed:.2f}")
print(f"  EV diario VIP (9 sesiones):    ${ev_fixed * N_VIP:.2f}")
print(f"  Sesiones ganadoras para $60:   {int(META/fixed_win)} wins")
print()

# Cuantos dias consecutivos de losses pueden ocurrir?
# Probabilidad de X dias seguidos con EV negativo:
# Asumimos que un dia es "malo" si caen >= 2 sesiones perdidas
p_bad_day = 1 - ((1 - (1-P_SES))**N_VIP)  # al menos 1 sesion fallida entre 9
print(f"  P(al menos 1 sesion fallida en un dia VIP): {p_bad_day*100:.1f}%")
p_2bad = comb(N_VIP, 2) * ((1-P_SES)**2) * (P_SES**(N_VIP-2))
print(f"  P(exactamente 2 sesiones fallidas en un dia): {p_2bad*100:.2f}%")
p_day_loss = (1-P_SES)**N_VIP  # todos pierden (muy raro)
print(f"  P(todas las sesiones del dia fallan): {p_day_loss*100:.6f}%")
print()

print("=" * 58)
print("  TABLA: primera apuesta segun capital disponible")
print("=" * 58)
print()
print(f"  {'Capital':>10}  {'1ra apuesta':>12}  {'Gana/sesion':>12}  {'Pierde/sesion':>14}")
print("  " + "-" * 52)
for cap in [100, 150, 200, 250, 300, 400, 500, 1000]:
    # Con target fijo proporcional: ganar siempre meta/3 por sesion (3 wins bastan)
    win_target = META / 3  # necesito 3 wins para cubrir meta (con 9 sesiones hay margen)
    # Para capital = 300, win_target = 20. Para otros capitales, escalar.
    w_t = cap * (win_target / CAPITAL)
    bet = w_t / (PAYOUT * 2)
    loss = w_t * REAL_LOSS_RATIO
    print(f"  ${cap:>9.0f}  ${bet:>11.2f}  ${w_t:>11.2f}  ${loss:>13.2f}")

print()
print("=" * 58)
print("  PLAN OPERATIVO FINAL")
print("=" * 58)
print()
print("  Capital inicial:         $300")
print("  Meta diaria:             +$60 (20%)")
print("  Stop-loss diario:        -$60 (20%) - NO seguir ese dia")
print()
print("  REGLA: cada sesion configura el Masaniello para ganar $20")
print("  con las siguientes apuestas (base sobre $300):")
print()
# Masaniello calibrado para $20 ganancia en 6 ops, 2 wins, payout 92%
# La progresion de stakes es: apuesta1, apuesta2, ... calculadas por el sistema
# Primera apuesta: $20 / (1.92 * 2) = $5.21
bet1 = fixed_win / (1 + PAYOUT) / W
bet2 = bet1 * 1.8   # tipico multiplicador gale
bet3 = bet2 * 1.8   # segundo gale
print(f"  - Senal 1:  ${bet1:.2f} (entrada)")
print(f"  - Senal 1G: ${bet2:.2f} (1er gale si pierde)")
print(f"  - Senal 1GG:${bet3:.2f} (2do gale si pierde 2 veces)")
print(f"  - ... (Masaniello ajusta automaticamente cada senal)")
print()
print("  RESULTADO ESPERADO:")
print(f"  - Dias con meta alcanzada: ~{P_SES*100:.0f}% (gana sesion, repite)")
print(f"  - Dias con perdida grande: ~{(1-P_SES)**2 * 100:.2f}% (2 sesiones perdidas el mismo dia)")
print()
print("  Con 9 sesiones VIP: basta ganar 3 sesiones para cubrir la")
print("  meta de $60. Las 6 restantes son 'colchon' de seguridad.")
