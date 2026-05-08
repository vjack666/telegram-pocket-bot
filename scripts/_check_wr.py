import re
from pathlib import Path
from collections import Counter

content = Path('ejemplo.md').read_text(encoding='utf-8')

date_pat = re.compile(r'^\[(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}:\d{2})\]')
res_pat  = re.compile(r'(VICTORIA DIRECTA|VICTORIA EN 1.*?MARTINGALA|VICTORIA EN 2.*?MARTINGALA|P[EÉ]RDIDA)', re.IGNORECASE)
label_map = {'victoria directa':'WD','victoria en 1':'G1','victoria en 2':'G2','perdida':'L','pérdida':'L'}

outcomes = []
for line in content.splitlines():
    if not date_pat.match(line): continue
    res_m = res_pat.search(line)
    if not res_m: continue
    raw = res_m.group(1).lower()
    label = next((v for k,v in label_map.items() if raw.startswith(k)), '?')
    outcomes.append(label)

cnt = Counter(outcomes)
total = len(outcomes)
wins = cnt['WD'] + cnt['G1'] + cnt['G2']
print(f"Total señales (con timestamp): {total}")
print(f"WD={cnt['WD']} ({cnt['WD']/total*100:.1f}%)  G1={cnt['G1']} ({cnt['G1']/total*100:.1f}%)  G2={cnt['G2']} ({cnt['G2']/total*100:.1f}%)  L={cnt['L']} ({cnt['L']/total*100:.1f}%)")
print(f"WR real = {wins}/{total} = {wins/total*100:.2f}%")
print()

PM=1.92; NET=0.92; r=PM/NET; tm=1+r+r**2
wr = wins/total
for cap_pct in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]:
    cap_abs = 300*cap_pct
    entry = cap_abs/tm
    gain = entry*NET
    loss = cap_abs
    ev = wr*gain - (1-wr)*loss
    bk = loss/(gain+loss)
    print(f"cap={cap_pct*100:.0f}%  entry=${entry:.2f}  gain/win=${gain:.2f}  loss/L=${loss:.2f}  EV/señal=${ev:.3f}  breakeven_WR={bk*100:.1f}%")
