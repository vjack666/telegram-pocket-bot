import re
from pathlib import Path

content = Path('ejemplo.md').read_text(encoding='utf-8', errors='ignore')
results = re.findall(r'(VICTORIA DIRECTA|VICTORIA EN 1.*?MARTINGALA|VICTORIA EN 2.*?MARTINGALA|RDIDA)', content)

payout = 0.92
calc_max_steps = 3
calc_increment = 13
start = 300.0

def calc_amounts(balance):
    target = float(int(balance) + calc_increment)
    risk_cap = round(max(0.01, balance * 0.10), 2)
    amounts = []
    bal = balance
    cap = False
    for _ in range(calc_max_steps):
        if cap:
            amount = risk_cap
        else:
            needed = max(0.0, target - bal)
            amount = round(max(0.01, needed / payout), 2)
            if amount > risk_cap:
                amount = risk_cap
                cap = True
        amounts.append(amount)
        bal = max(0.0, bal - amount)
    return amounts

bal = start
min_bal = bal
bust = 0
wd = w1 = w2 = ls = 0
total_ops = 0

for i, r in enumerate(results, start=1):
    a1, a2, a3 = calc_amounts(bal)
    if 'VICTORIA DIRECTA' in r:
        pnl = a1 * payout
        wd += 1
    elif 'VICTORIA EN 1' in r:
        pnl = -a1 + a2 * payout
        w1 += 1
    elif 'VICTORIA EN 2' in r:
        pnl = -a1 - a2 + a3 * payout
        w2 += 1
    else:
        pnl = -(a1 + a2 + a3)
        ls += 1
    bal += pnl
    total_ops = i
    if bal < min_bal:
        min_bal = bal
    if bal <= 0:
        bust = i
        break

a_ref = calc_amounts(start)
print("=== NUEVA LOGICA: reinicio completo por senal (sin deuda acumulada) ===")
print("Balance inicial: $" + str(round(start, 2)))
print("Balance final:   $" + str(round(bal, 2)))
print("Balance minimo:  $" + str(round(min_bal, 2)))
if bust == 0:
    print("Quiebra:         No - completo " + str(total_ops) + " senales")
else:
    print("Quiebra:         Op #" + str(bust) + " de " + str(total_ops))
print("")
print("Victoria directa: " + str(wd))
print("Victoria G1:      " + str(w1))
print("Victoria G2:      " + str(w2))
print("Perdida total:    " + str(ls))
print("Total senales:    " + str(total_ops))
print("")
print("Montos con balance=$300:  E=$" + str(a_ref[0]) + "  G1=$" + str(a_ref[1]) + "  G2=$" + str(a_ref[2]))
