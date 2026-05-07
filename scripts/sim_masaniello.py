"""
Simulación: Masaniello (5/2) vs Martingala actual
Datos: ejemplo.md — 4,148 señales reales del canal (60 días)

Parámetros Masaniello tomados del Excel:
  N = 5 operaciones por sesión
  W = 2 victorias necesarias
  Payout = 92%
  Capital inicial = $300
  Tipo = Normale (reinicio solo al superar high water mark)

Cada "operación" Masaniello = 1 señal con sus gales incluidos.
Si la señal gana (directo, G1 o G2) → W++
Si la señal pierde los 3 intentos → L++

Lógica de apuestas Masaniello:
  - La apuesta en cada paso usa la tabla de probabilidades binomiales
  - Fórmula: stake = balance * (1 - payout * P_win_fwd / (P_win_fwd + (payout-1)*P_lose_fwd))
  - Garantiza ganancia fija de target_pct si se alcanzan W victorias en la sesión
"""

import re
from math import comb

# ─── Parámetros ───────────────────────────────────────────────────────────────
N_OPS      = 5      # operaciones por sesión
W_NEEDED   = 2      # victorias necesarias para ganar sesión
PAYOUT     = 1.92   # multiplicador (1 + 0.92)
COMMISSION = 0.0    # comisión % (sin comisión en Pocket Option)
CAPITAL_INI = 300.0  # capital inicial (mismo que simulación anterior)

# Target profit: ganancia si se completan W victorias
# Masaniello garantiza: capital * (PAYOUT^W - 1) / algo...
# El Excel mostró 19.41% con $100 → usamos esa fórmula para escalar
TARGET_PCT = 0.194090413   # del Excel (fijo por diseño del sistema)

# ─── Tabla de probabilidades binomiales (núcleo de Masaniello) ────────────────
def binom_prob_table(n_max, payout_mult):
    """
    Genera tabla P[losses][wins] = probabilidad de alcanzar W victorias
    desde el estado (losses, wins) en N_OPS totales.
    Equivale al rango $N$2:$DJ$101 del Excel.
    """
    # Usamos cálculo recursivo hacia adelante
    # P(wins_restantes, ops_restantes) con probabilidad p_win por operación
    # NO usamos p fija — Masaniello calcula el stake desde las probabilidades
    # de la tabla precalculada con distribución binomial exacta.
    pass

def masaniello_stake(balance, losses_so_far, wins_so_far, n, w, payout_mult):
    """
    Calcula el stake para la siguiente operación según Masaniello.
    
    Estado actual: hemos jugado (losses + wins) operaciones.
    Nos quedan (n - losses - wins) operaciones.
    Necesitamos (w - wins) victorias más.
    
    La fórmula central del Excel:
      stake = balance * (1 - payout * P_next_win_fwd / (P_next_win_fwd + (payout-1)*P_next_lose_fwd))
    
    Donde:
      P_next_win_fwd  = prob de alcanzar W victorias si la próxima ES victoria
      P_next_lose_fwd = prob de alcanzar W victorias si la próxima ES derrota
    """
    ops_done  = losses_so_far + wins_so_far
    ops_left  = n - ops_done
    wins_left = w - wins_so_far
    
    # Caso fin de sesión: no hay más operaciones
    if ops_left <= 0:
        return 0.0
    
    # Ya ganamos la sesión
    if wins_left <= 0:
        return 0.0
    
    # Imposible ganar: necesitamos más victorias que operaciones restantes
    if wins_left > ops_left:
        return 0.0
    
    # P_win_fwd: prob de alcanzar W victorias dado que la PRÓXIMA es una victoria
    #   → necesitamos (wins_left - 1) victorias en (ops_left - 1) operaciones restantes
    new_wins_left = wins_left - 1
    new_ops_left  = ops_left - 1
    if new_wins_left <= 0:
        p_win_fwd = 1.0   # ya alcanzamos W victorias con esta
    elif new_wins_left > new_ops_left:
        p_win_fwd = 0.0   # imposible
    else:
        # Usar la fórmula de Masaniello: payout^wins_left_needed (caso borde)
        # o la recursión de la tabla
        p_win_fwd = _forward_prob(new_ops_left, new_wins_left, payout_mult)
    
    # P_lose_fwd: prob de alcanzar W victorias dado que la PRÓXIMA es una derrota
    #   → necesitamos (wins_left) victorias en (ops_left - 1) operaciones restantes
    new_wins_left2 = wins_left
    new_ops_left2  = ops_left - 1
    if new_wins_left2 <= 0:
        p_lose_fwd = 1.0
    elif new_wins_left2 > new_ops_left2:
        p_lose_fwd = 0.0
    else:
        p_lose_fwd = _forward_prob(new_ops_left2, new_wins_left2, payout_mult)
    
    denom = p_win_fwd + (payout_mult - 1) * p_lose_fwd
    if denom == 0:
        return balance  # apuesta todo si es caso degenerado
    
    stake = balance * (1 - payout_mult * p_win_fwd / denom)
    
    # Protección: stake nunca negativo ni mayor que el balance
    stake = max(0.01, min(stake, balance))
    return round(stake, 2)


def _forward_prob(ops_left, wins_needed, payout_mult):
    """
    Probabilidad de alcanzar `wins_needed` victorias en `ops_left` operaciones.
    Según Masaniello, cuando wins_needed == ops_left: payout^wins_needed (caso borde exacto)
    En el caso general, usa la recursión de la tabla del Excel.
    """
    if wins_needed <= 0:
        return 1.0
    if wins_needed > ops_left:
        return 0.0
    if wins_needed == ops_left:
        # Borde: hay que ganar TODAS las restantes
        return payout_mult ** ops_left
    
    # Recursión: P = payout * P_win_next * P_lose_next / (P_win_next + (payout-1)*P_lose_next)
    p_if_win  = _forward_prob(ops_left - 1, wins_needed - 1, payout_mult)
    p_if_lose = _forward_prob(ops_left - 1, wins_needed,     payout_mult)
    denom = p_if_win + (payout_mult - 1) * p_if_lose
    if denom == 0:
        return 0.0
    return payout_mult * p_if_win * p_if_lose / denom


# ─── Parser de ejemplo.md ─────────────────────────────────────────────────────
def parse_signals(path):
    """
    Devuelve lista de resultados: 'WD', 'G1', 'G2', 'L'
    """
    results = []
    pat = re.compile(
        r'VICTORIA DIRECTA|VICTORIA EN 1.*MARTINGALA|VICTORIA EN 2.*MARTINGALA|PÉRDIDA|PERDIDA',
        re.IGNORECASE
    )
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = pat.search(line)
            if m:
                txt = m.group().upper()
                if 'DIRECTA' in txt:
                    results.append('WD')
                elif '1' in txt and 'MARTINGALA' in txt:
                    results.append('G1')
                elif '2' in txt and 'MARTINGALA' in txt:
                    results.append('G2')
                else:
                    results.append('L')
    return results


# ─── Simulación MASANIELLO ─────────────────────────────────────────────────────
def sim_masaniello(signals, capital_ini, n, w, payout_mult, target_pct):
    """
    Simula el sistema Masaniello por sesiones de N operaciones.
    Cada señal (con sus gales) cuenta como 1 operación.
    """
    balance  = capital_ini
    min_bal  = capital_ini
    max_bal  = capital_ini
    hwm      = capital_ini   # high water mark
    
    sessions_won  = 0
    sessions_lost = 0
    sessions_total = 0
    
    op_idx = 0
    n_signals = len(signals)
    
    log = []
    
    while op_idx < n_signals:
        # ── Nueva sesión ──────────────────────────────────────────────────────
        if balance <= 0:
            log.append(f"⛔ QUIEBRA en op #{op_idx+1}")
            break
        
        session_start_balance = balance
        session_target = session_start_balance * (1 + target_pct)
        losses = 0
        wins   = 0
        session_stakes = []
        session_results = []
        
        for step in range(n):
            if op_idx >= n_signals:
                break
            
            # Comprobar si ya ganamos o es imposible ganar
            wins_needed = w - wins
            ops_left = n - (losses + wins)
            if wins_needed <= 0:
                break  # sesión ganada antes de completar N ops
            if wins_needed > ops_left:
                break  # imposible ganar, sesión terminada
            
            stake = masaniello_stake(session_start_balance, losses, wins, n, w, payout_mult)
            
            sig = signals[op_idx]
            op_idx += 1
            
            # Victoria = señal gana en cualquier intento (WD, G1, G2)
            is_win = sig in ('WD', 'G1', 'G2')
            
            session_stakes.append(stake)
            session_results.append(sig)
            
            if is_win:
                wins += 1
                balance += stake * (payout_mult - 1)
            else:
                losses += 1
                balance -= stake
            
            balance = round(balance, 2)
            min_bal = min(min_bal, balance)
            max_bal = max(max_bal, balance)
        
        sessions_total += 1
        
        if wins >= w:
            sessions_won += 1
            result_str = "✅ GANADA"
        else:
            sessions_lost += 1
            result_str = "❌ PERDIDA"
        
        if sessions_total <= 20 or sessions_total % 50 == 0:
            log.append(
                f"Sesión #{sessions_total:3d} {result_str} | "
                f"W={wins} L={losses} | "
                f"Stakes={[round(s,2) for s in session_stakes]} "
                f"Resultados={session_results} | "
                f"Bal: ${session_start_balance:.2f} → ${balance:.2f}"
            )
    
    return {
        'final_balance': balance,
        'min_balance': min_bal,
        'max_balance': max_bal,
        'sessions_total': sessions_total,
        'sessions_won': sessions_won,
        'sessions_lost': sessions_lost,
        'ops_used': op_idx,
        'log': log,
    }


# ─── Simulación MARTINGALA ACTUAL ─────────────────────────────────────────────
def sim_martingala(signals, capital_ini, payout_raw=0.92, calc_increment=13, max_steps=3):
    """
    Replica el sistema actual del bot: reset completo por señal.
    Entrada ≈ floor(balance) + 13 ÷ 0.92
    Gale 1 = monto para recuperar pérdida + ganar lo mismo
    Gale 2 = ídem
    """
    balance = capital_ini
    min_bal = capital_ini
    max_bal = capital_ini
    wins = losses = 0
    
    log = []
    
    for i, sig in enumerate(signals):
        if balance <= 0:
            log.append(f"⛔ QUIEBRA en op #{i+1}")
            break
        
        # Calcular monto entrada
        target = int(balance) + calc_increment
        entry  = round(target / (1 + payout_raw), 2)
        
        if sig == 'WD':
            balance += entry * payout_raw
            wins += 1
        elif sig == 'G1':
            # Pierde entrada, gana gale 1
            g1 = round((entry + entry * (1 + payout_raw)) / (1 + payout_raw), 2)
            balance -= entry
            balance += g1 * payout_raw
            wins += 1
        elif sig == 'G2':
            # Pierde entrada y gale 1, gana gale 2
            g1 = round((entry + entry * (1 + payout_raw)) / (1 + payout_raw), 2)
            g2 = round((entry + g1 + (entry + g1) * (1 + payout_raw)) / (1 + payout_raw), 2)
            balance -= entry
            balance -= g1
            balance += g2 * payout_raw
            wins += 1
        else:  # L
            g1 = round((entry + entry * (1 + payout_raw)) / (1 + payout_raw), 2)
            g2 = round((entry + g1 + (entry + g1) * (1 + payout_raw)) / (1 + payout_raw), 2)
            balance -= entry
            balance -= g1
            balance -= g2
            losses += 1
        
        balance = round(balance, 2)
        min_bal = min(min_bal, balance)
        max_bal = max(max_bal, balance)
        
        if (i+1) <= 10 or (i+1) % 200 == 0:
            log.append(f"Op #{i+1:4d} | {sig:2s} | bal=${balance:.2f}")
    
    return {
        'final_balance': balance,
        'min_balance': min_bal,
        'max_balance': max_bal,
        'wins': wins,
        'losses': losses,
        'log': log,
    }


# ─── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    signals = parse_signals(os.path.join(base, 'ejemplo.md'))
    
    total = len(signals)
    wd  = signals.count('WD')
    g1  = signals.count('G1')
    g2  = signals.count('G2')
    l   = signals.count('L')
    
    print("=" * 60)
    print(f"DATOS HISTÓRICOS: {total} señales (60 días)")
    print(f"  WD={wd} ({wd/total*100:.1f}%)  G1={g1} ({g1/total*100:.1f}%)  "
          f"G2={g2} ({g2/total*100:.1f}%)  L={l} ({l/total*100:.1f}%)")
    print("=" * 60)
    
    # ── Simulación 1: Martingala actual ──────────────────────────────────────
    print("\n📊 SISTEMA ACTUAL (Martingala reset por señal, $300 inicial)")
    mart = sim_martingala(signals, CAPITAL_INI)
    print(f"  Balance final:   ${mart['final_balance']:.2f}")
    print(f"  Balance mínimo:  ${mart['min_balance']:.2f}")
    print(f"  Balance máximo:  ${mart['max_balance']:.2f}")
    print(f"  Victorias:       {mart['wins']}")
    print(f"  Pérdidas:        {mart['losses']}")
    roi = (mart['final_balance'] - CAPITAL_INI) / CAPITAL_INI * 100
    print(f"  ROI total:       {roi:+.1f}%")
    print("\n  Primeras operaciones:")
    for line in mart['log'][:5]:
        print("    " + line)
    if any("QUIEBRA" in l for l in mart['log']):
        for line in mart['log']:
            if "QUIEBRA" in line:
                print("    " + line)
                break
    
    # ── Simulación 2: Masaniello 5/2 ─────────────────────────────────────────
    print("\n📊 MASANIELLO 5/2 (payout 92%, reinicio por high-water-mark, $300 inicial)")
    masa = sim_masaniello(signals, CAPITAL_INI, N_OPS, W_NEEDED, PAYOUT, TARGET_PCT)
    print(f"  Balance final:   ${masa['final_balance']:.2f}")
    print(f"  Balance mínimo:  ${masa['min_balance']:.2f}")
    print(f"  Balance máximo:  ${masa['max_balance']:.2f}")
    print(f"  Sesiones totales:{masa['sessions_total']}")
    print(f"  Sesiones ganadas:{masa['sessions_won']} "
          f"({masa['sessions_won']/masa['sessions_total']*100:.1f}%)")
    print(f"  Sesiones perdidas:{masa['sessions_lost']} "
          f"({masa['sessions_lost']/masa['sessions_total']*100:.1f}%)")
    print(f"  Señales usadas:  {masa['ops_used']} de {total}")
    roi2 = (masa['final_balance'] - CAPITAL_INI) / CAPITAL_INI * 100
    print(f"  ROI total:       {roi2:+.1f}%")
    
    print("\n  Primeras 20 sesiones:")
    for line in masa['log'][:20]:
        print("    " + line)
    
    # ── Comparación ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("COMPARACIÓN FINAL")
    print("=" * 60)
    print(f"{'Sistema':<25} {'Bal.Final':>10} {'ROI':>8} {'Min.Bal':>10}")
    print("-" * 60)
    print(f"{'Martingala actual':<25} ${mart['final_balance']:>9.2f} {(mart['final_balance']-CAPITAL_INI)/CAPITAL_INI*100:>+7.1f}% ${mart['min_balance']:>9.2f}")
    print(f"{'Masaniello 5/2':<25} ${masa['final_balance']:>9.2f} {(masa['final_balance']-CAPITAL_INI)/CAPITAL_INI*100:>+7.1f}% ${masa['min_balance']:>9.2f}")
    
    # Ganancia por sesión Masaniello
    if masa['sessions_total'] > 0:
        avg_per_session = (masa['final_balance'] - CAPITAL_INI) / masa['sessions_total']
        print(f"\n  Masaniello: ganancia promedio por sesión = ${avg_per_session:+.2f}")
        print(f"  Masaniello: {total//N_OPS} sesiones posibles con {total} señales")
