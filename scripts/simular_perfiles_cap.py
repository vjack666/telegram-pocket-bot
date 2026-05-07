"""
Simulación comparativa de perfiles de cap sobre el histórico real.

Perfiles:
  uncapped  — sin cap (recovery completo, Masaniello puro)
  cap10     — max_trade_pct=10% (sistema actual)
  cap8      — max_trade_pct=8%
  cap6      — max_trade_pct=6%  (punto de ruptura esperado)

Métricas por perfil:
  balance_final, meta_dias (de N), sesiones_negativas,
  max_dd, max_dd_pct, wr_efectivo, total_ops

Ejecutar:
  .venv\Scripts\python.exe scripts/simular_perfiles_cap.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HISTORY     = ROOT / "ejemplo.md"
N_OPS       = 12
W_NEEDED    = 4
PAYOUT_MULT = 1.92          # total return (1 + net_payout)
NET_PAYOUT  = PAYOUT_MULT - 1.0   # 0.92
BASE_BALANCE = 300.0
META_DIARIA  = 60.0

# RecoveryProfile: multiplicadores congelados (igual que en producción).
# Auto-calculados desde payout — mismo comportamiento que .env vacío.
G1_MULT = round(PAYOUT_MULT / NET_PAYOUT, 4)   # 2.0870
G2_MULT = round(G1_MULT * G1_MULT, 4)          # 4.3556


# ── Parser ──────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    timestamp: datetime
    result: str     # WD | G1 | G2 | L
    is_win: bool


def parse_outcomes(path: Path) -> list[Outcome]:
    date_pat = re.compile(r"^\[(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}:\d{2})\]")
    res_pat  = re.compile(
        r"(VICTORIA DIRECTA|VICTORIA EN 1.*?MARTINGALA|VICTORIA EN 2.*?MARTINGALA|P[ÉE]RDIDA)",
        re.IGNORECASE,
    )
    label_map = {
        "victoria directa": "WD",
        "victoria en 1":    "G1",
        "victoria en 2":    "G2",
        "perdida":          "L",
        "pérdida":          "L",
    }
    outcomes: list[Outcome] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        ts_m  = date_pat.match(line)
        res_m = res_pat.search(line)
        if not ts_m or not res_m:
            continue
        ts = datetime.strptime(f"{ts_m.group(1)} {ts_m.group(2)}", "%d/%m/%Y %H:%M:%S")
        raw   = res_m.group(1).lower()
        label = next((v for k, v in label_map.items() if raw.startswith(k)), "?")
        outcomes.append(Outcome(ts, label, label != "L"))
    return outcomes


# ── Fórmula Masaniello ───────────────────────────────────────────────────────

def _fwd_prob(ops_left: int, wins_needed: int, pm: float) -> float:
    if wins_needed <= 0:        return 1.0
    if wins_needed > ops_left:  return 0.0
    if wins_needed == ops_left: return pm ** ops_left
    pw = _fwd_prob(ops_left - 1, wins_needed - 1, pm)
    pl = _fwd_prob(ops_left - 1, wins_needed,     pm)
    d  = pw + (pm - 1) * pl
    return (pm * pw * pl / d) if d else 0.0


def masaniello_stake(losses: int, wins: int) -> float:
    ops_left  = N_OPS - (losses + wins)
    wins_left = W_NEEDED - wins
    if ops_left <= 0 or wins_left <= 0 or wins_left > ops_left:
        return 0.0
    pw = _fwd_prob(ops_left - 1, wins_left - 1, PAYOUT_MULT)
    pl = _fwd_prob(ops_left - 1, wins_left,     PAYOUT_MULT)
    d  = pw + NET_PAYOUT * pl
    if not d:
        return BASE_BALANCE
    s = BASE_BALANCE * (1 - PAYOUT_MULT * pw / d)
    return round(max(0.01, min(s, BASE_BALANCE)), 2)


# ── Simulación de un perfil ─────────────────────────────────────────────────

@dataclass
class ProfileResult:
    label: str
    cap_pct: float | None   # None = uncapped
    balance_final: float
    meta_dias: int
    total_dias: int
    sesiones_negativas: int
    total_sesiones: int
    max_dd: float
    max_dd_pct: float       # % sobre BASE_BALANCE
    total_ops: int
    total_wins: int
    # detalle de caps activos
    caps_entry: int
    caps_g1: int
    caps_g2: int


def simulate_profile(outcomes: list[Outcome], cap_pct: float | None, label: str) -> ProfileResult:
    """
    cap_pct: fracción de BASE_BALANCE usada como cap por operación.
             None = sin cap (Masaniello puro).
    Los mults G1/G2 están congelados al inicio (igual que en producción).
    Cap se aplica ANTES del round en todos los pasos.
    """
    cap = round(BASE_BALANCE * cap_pct, 2) if cap_pct is not None else float("inf")

    balance = BASE_BALANCE
    peak    = balance
    max_dd  = 0.0

    sessions_raw = [outcomes[i:i + N_OPS] for i in range(0, len(outcomes), N_OPS)]

    daily_pnl: dict[str, float] = defaultdict(float)
    daily_done: set[str]        = set()

    total_sesiones    = 0
    sesiones_negativas = 0
    meta_dias_set:    set[str] = set()
    all_days:         set[str] = set()

    total_ops   = 0
    total_wins  = 0
    caps_entry  = 0
    caps_g1     = 0
    caps_g2     = 0

    for chunk in sessions_raw:
        if not chunk:
            continue
        day = chunk[0].timestamp.strftime("%d/%m/%Y")
        all_days.add(day)

        if day in daily_done:
            continue

        wins = losses = 0
        session_pnl = 0.0

        for outcome in chunk:
            wins_needed = W_NEEDED - wins
            ops_left    = N_OPS - (wins + losses)
            if wins_needed <= 0 or wins_needed > ops_left:
                break

            entry_raw = masaniello_stake(losses, wins)
            g1_raw    = entry_raw * G1_MULT
            g2_raw    = entry_raw * G2_MULT

            # Track caps
            if entry_raw > cap:  caps_entry += 1
            if g1_raw    > cap:  caps_g1    += 1
            if g2_raw    > cap:  caps_g2    += 1

            entry = round(min(entry_raw, cap), 2)

            total_ops += 1

            if outcome.is_win:
                wins       += 1
                total_wins += 1
                gain        = round(entry * NET_PAYOUT, 2)
                session_pnl += gain
                balance     = round(balance + gain, 2)
            else:
                losses      += 1
                session_pnl -= entry
                balance     = round(balance - entry, 2)

            peak   = max(peak, balance)
            dd     = round(peak - balance, 2)
            max_dd = max(max_dd, dd)

            if wins >= W_NEEDED:
                break

        session_pnl = round(session_pnl, 2)
        total_sesiones += 1
        if session_pnl < 0:
            sesiones_negativas += 1

        daily_pnl[day] = round(daily_pnl[day] + session_pnl, 2)
        if daily_pnl[day] >= META_DIARIA and day not in meta_dias_set:
            meta_dias_set.add(day)
            daily_done.add(day)

    return ProfileResult(
        label=label,
        cap_pct=cap_pct,
        balance_final=round(balance, 2),
        meta_dias=len(meta_dias_set),
        total_dias=len(all_days),
        sesiones_negativas=sesiones_negativas,
        total_sesiones=total_sesiones,
        max_dd=max_dd,
        max_dd_pct=round(max_dd / BASE_BALANCE * 100, 1),
        total_ops=total_ops,
        total_wins=total_wins,
        caps_entry=caps_entry,
        caps_g1=caps_g1,
        caps_g2=caps_g2,
    )


# ── Tabla de resultados ──────────────────────────────────────────────────────

def print_table(results: list[ProfileResult]) -> None:
    W = 13
    headers = [
        "Perfil", "Cap%", "Bal.Final", "Meta/Dias",
        "Ses.Neg.", "MaxDD", "MaxDD%", "WR%",
        "Ops", "CapEntry", "CapG1", "CapG2",
    ]
    sep = "+" + "+".join("-" * (W + 2) for _ in headers) + "+"
    header_row = "| " + " | ".join(h.center(W) for h in headers) + " |"

    print()
    print(f"  G1_MULT={G1_MULT}  G2_MULT={G2_MULT}  META_DIARIA={META_DIARIA}  BASE={BASE_BALANCE}")
    print()
    print(sep)
    print(header_row)
    print(sep)

    for r in results:
        cap_str  = f"{r.cap_pct*100:.0f}%" if r.cap_pct is not None else "sin cap"
        meta_str = f"{r.meta_dias}/{r.total_dias}"
        ses_str  = f"{r.sesiones_negativas}/{r.total_sesiones}"
        wr       = round(r.total_wins / r.total_ops * 100, 1) if r.total_ops else 0.0
        cols = [
            r.label[:W],
            cap_str,
            f"${r.balance_final:,.2f}",
            meta_str,
            ses_str,
            f"${r.max_dd:.2f}",
            f"{r.max_dd_pct}%",
            f"{wr}%",
            str(r.total_ops),
            str(r.caps_entry),
            str(r.caps_g1),
            str(r.caps_g2),
        ]
        row = "| " + " | ".join(c.center(W) for c in cols) + " |"
        print(row)

    print(sep)
    print()
    print("  CapEntry/G1/G2 = veces que el cap truncó ese paso (0 = recovery completo en ese paso)")
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not HISTORY.exists():
        print(f"ERROR: No se encuentra {HISTORY}")
        sys.exit(1)

    print(f"Parseando {HISTORY.name} ...", end=" ", flush=True)
    outcomes = parse_outcomes(HISTORY)
    print(f"{len(outcomes)} resultados.")

    profiles = [
        ("uncapped", None),
        ("cap 10%",  0.10),
        ("cap 8%",   0.08),
        ("cap 6%",   0.06),
    ]

    results = []
    for label, cap_pct in profiles:
        r = simulate_profile(outcomes, cap_pct, label)
        results.append(r)
        print(f"  [{label}] bal={r.balance_final}  meta={r.meta_dias}/{r.total_dias}  dd={r.max_dd}")

    print_table(results)

    # Análisis de degradación entre perfiles
    baseline = next(r for r in results if r.cap_pct is None)
    print("  Degradación relativa vs uncapped:")
    for r in results:
        if r is baseline:
            continue
        delta_bal  = round(r.balance_final - baseline.balance_final, 2)
        delta_meta = r.meta_dias - baseline.meta_dias
        delta_dd   = round(r.max_dd - baseline.max_dd, 2)
        print(
            f"    {r.label:10s}  bal={delta_bal:+,.2f}  "
            f"meta={delta_meta:+d}/{baseline.total_dias}  dd={delta_dd:+.2f}"
        )
    print()


if __name__ == "__main__":
    main()
