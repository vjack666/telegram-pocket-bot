"""
Grafica detallada: balance tras cada operacion individual.
Cada punto = una operacion ejecutada (win o loss).
Lineas verticales grises = cambio de sesion.
Lineas verticales azules = cambio de dia.
Meta diaria ($60 sobre banca del dia) marcada con banda verde.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sim_objetivo_60_por_dia import (
    CAPITAL_INICIAL,
    HISTORY,
    META_DIARIA,
    N_OPS,
    PAYOUT_MULT,
    W_NEEDED,
    group_sessions,
    masaniello_stake,
    parse_outcomes,
)

BASE_STAKE_BALANCE = 300.0

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
import numpy as np

# ─── Reproducir la simulacion operacion a operacion ──────────────────────────
outcomes = parse_outcomes(HISTORY)
sessions = group_sessions(outcomes, N_OPS)

balance = float(CAPITAL_INICIAL)

# Listas para la curva detallada
op_indices: list[int] = []          # numero secuencial de operacion
op_balances: list[float] = []       # balance DESPUES de la operacion
op_colors: list[str] = []           # 'win' | 'loss'
op_timestamps: list[datetime] = []
op_stakes: list[float] = []
op_results: list[str] = []

# Para anotaciones de sesion/dia
session_start_indices: list[int] = []  # indice en op_indices donde empieza sesion
day_start_indices: list[int] = []      # indice donde empieza un nuevo dia
day_labels: list[str] = []
daily_start_balances: list[float] = []

from collections import defaultdict

daily_seen: set[str] = set()
daily_meta: dict[str, float] = {}  # dia -> balance al que se marca meta
daily_cumulative_pnl: dict[str, float] = defaultdict(float)
daily_start_bal: dict[str, float] = {}
meta_hit_days: set[str] = set()

op_counter = 0
WIN_RESULTS = {"WD", "G1", "G2"}

for session_index, chunk in enumerate(sessions, start=1):
    day = chunk[0].timestamp.strftime("%d/%m/%Y")

    if day not in daily_seen:
        daily_seen.add(day)
        day_start_indices.append(op_counter)
        day_labels.append(day)
        daily_start_balances.append(balance)
        daily_start_bal[day] = balance

    if day in meta_hit_days:
        continue

    session_start_indices.append(op_counter)

    wins = 0
    losses = 0

    for outcome in chunk:
        wins_needed = W_NEEDED - wins
        ops_left = N_OPS - (wins + losses)
        if wins_needed <= 0 or wins_needed > ops_left:
            break

        stake = masaniello_stake(BASE_STAKE_BALANCE, losses, wins, N_OPS, W_NEEDED, PAYOUT_MULT)

        is_win = outcome.result in WIN_RESULTS
        if is_win:
            pnl_op = stake * (PAYOUT_MULT - 1)
            wins += 1
        else:
            pnl_op = -stake
            losses += 1

        balance = round(balance + pnl_op, 2)
        daily_cumulative_pnl[day] = round(daily_cumulative_pnl[day] + pnl_op, 2)

        op_indices.append(op_counter)
        op_balances.append(balance)
        op_colors.append("win" if is_win else "loss")
        op_timestamps.append(outcome.timestamp)
        op_stakes.append(stake)
        op_results.append(outcome.result)
        op_counter += 1

        if wins >= W_NEEDED:
            break

    # Comprobar si se alcanzo meta del dia en esta sesion
    if daily_cumulative_pnl[day] >= META_DIARIA and day not in meta_hit_days:
        meta_hit_days.add(day)

# ─── Figura ──────────────────────────────────────────────────────────────────
n_ops = len(op_indices)
x = np.array(op_indices)
y = np.array(op_balances)

WIN_COLOR  = "#4CAF50"
LOSS_COLOR = "#F44336"
LINE_COLOR = "#1565C0"

fig, ax = plt.subplots(figsize=(22, 8))

# Linea del balance
ax.plot(x, y, color=LINE_COLOR, linewidth=0.8, alpha=0.7, zorder=2)

# Scatter wins / losses
win_mask  = np.array([c == "win"  for c in op_colors])
loss_mask = np.array([c == "loss" for c in op_colors])

ax.scatter(x[win_mask],  y[win_mask],  color=WIN_COLOR,  s=18, zorder=4, label="Victoria")
ax.scatter(x[loss_mask], y[loss_mask], color=LOSS_COLOR, s=18, zorder=4, label="Derrota")

# Separadores de dia (linea azul vertical)
for i, (idx, lbl) in enumerate(zip(day_start_indices, day_labels)):
    ax.axvline(idx, color="#90CAF9", linewidth=0.7, linestyle="--", zorder=1)
    # Etiqueta del dia cada N dias para no saturar
    if i % 5 == 0:
        ax.text(
            idx + 1, ax.get_ylim()[0] if i == 0 else min(y) * 0.998,
            lbl, fontsize=6, color="#1565C0", rotation=90, va="bottom", ha="left",
        )

# Linea de capital inicial
ax.axhline(CAPITAL_INICIAL, color="#FF9800", linewidth=1.2, linestyle=":", alpha=0.8, label=f"Capital inicial (${CAPITAL_INICIAL:.0f})")

# Anotacion balance final
ax.annotate(
    f"${y[-1]:,.2f}",
    xy=(x[-1], y[-1]),
    xytext=(-80, 15),
    textcoords="offset points",
    fontsize=9,
    color=LINE_COLOR,
    fontweight="bold",
    arrowprops=dict(arrowstyle="->", color=LINE_COLOR, lw=1.2),
)

# Estadisticas en texto
wins_total  = sum(win_mask)
losses_total = sum(loss_mask)
win_rate = wins_total / n_ops * 100

info_text = (
    f"Ops totales: {n_ops}  |  "
    f"Victorias: {wins_total} ({win_rate:.1f}%)  |  "
    f"Derrotas: {losses_total} ({100-win_rate:.1f}%)  |  "
    f"Balance final: ${y[-1]:,.2f}  |  "
    f"Dias operados: {len(daily_seen)}"
)
fig.text(0.5, 0.01, info_text, ha="center", fontsize=9, color="#555")

# Labels y formato
ax.set_xlabel("Número de operacion", fontsize=10)
ax.set_ylabel("Balance ($)", fontsize=10)
ax.set_title(
    "Historial completo operacion a operacion — Masaniello 6/2 — Meta $60/dia\n"
    "Canales: VIP TRADER A + SMART SIGNALS | 17 Mar → 7 May 2026",
    fontsize=12,
    fontweight="bold",
)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
ax.legend(fontsize=9, loc="upper left")
ax.grid(True, alpha=0.25, axis="y")
ax.set_xlim(0, n_ops + 2)

plt.tight_layout(rect=[0, 0.04, 1, 1])

out = ROOT / "runtime" / "grafica_detallada_ops.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"Grafica guardada en: {out}")
print(f"  Operaciones totales: {n_ops}")
print(f"  Victorias: {wins_total} ({win_rate:.1f}%)")
print(f"  Derrotas:  {losses_total} ({100-win_rate:.1f}%)")
print(f"  Balance final: ${y[-1]:,.2f}")
print(f"  Dias operados: {len(daily_seen)}")
