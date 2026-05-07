"""
Grafica detallada operacion a operacion con REGLA CONSERVADORA:
  - Si el dia tiene balance inicial < $600: meta $40, cortar al 1er loss
  - Si balance inicial >= $600: meta $60, sin corte
Cada punto = una operacion ejecutada (win o loss).
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
    N_OPS,
    PAYOUT_MULT,
    W_NEEDED,
    group_sessions,
    masaniello_stake,
    parse_outcomes,
)

BASE = 300.0
WIN_RESULTS = {"WD", "G1", "G2"}

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
import numpy as np
from collections import defaultdict

outcomes = parse_outcomes(HISTORY)
sessions = group_sessions(outcomes, N_OPS)

balance = float(CAPITAL_INICIAL)

op_indices: list[int] = []
op_balances: list[float] = []
op_colors: list[str] = []
op_timestamps: list[datetime] = []

day_start_indices: list[int] = []
day_labels: list[str] = []
day_phases: list[str] = []   # "conservador" | "normal"
cut_indices: list[int] = []   # posicion donde el dia fue cortado

daily_seen: set[str] = set()
daily_pnl: dict[str, float] = defaultdict(float)
daily_start_bal: dict[str, float] = {}
meta_hit_days: set[str] = set()
cut_days: set[str] = set()

op_counter = 0

for chunk in sessions:
    day = chunk[0].timestamp.strftime("%d/%m/%Y")

    if day not in daily_seen:
        daily_seen.add(day)
        day_start_indices.append(op_counter)
        day_labels.append(day)
        daily_start_bal[day] = balance
        day_phases.append("conservador" if balance < 600 else "normal")

    if day in meta_hit_days or day in cut_days:
        continue

    conservative = daily_start_bal[day] < 600
    target = 40.0 if conservative else 60.0

    wins = losses = 0

    for outcome in chunk:
        wins_needed = W_NEEDED - wins
        ops_left = N_OPS - (wins + losses)
        if wins_needed <= 0 or wins_needed > ops_left:
            break

        stake = masaniello_stake(BASE, losses, wins, N_OPS, W_NEEDED, PAYOUT_MULT)
        is_win = outcome.result in WIN_RESULTS
        pnl_op = stake * (PAYOUT_MULT - 1) if is_win else -stake

        balance = round(balance + pnl_op, 2)
        daily_pnl[day] = round(daily_pnl[day] + pnl_op, 2)

        op_indices.append(op_counter)
        op_balances.append(balance)
        op_colors.append("win" if is_win else "loss")
        op_timestamps.append(outcome.timestamp)
        op_counter += 1

        if is_win:
            wins += 1
        else:
            losses += 1

        if conservative and losses >= 1:
            cut_indices.append(op_counter - 1)
            cut_days.add(day)
            break

        if wins >= W_NEEDED:
            break

    if daily_pnl[day] >= target:
        meta_hit_days.add(day)

# ─── Grafica ─────────────────────────────────────────────────────────────────
n_ops = len(op_indices)
x = np.array(op_indices)
y = np.array(op_balances)

WIN_COLOR  = "#4CAF50"
LOSS_COLOR = "#F44336"
CUT_COLOR  = "#FF9800"
LINE_COLOR = "#1565C0"

fig, ax = plt.subplots(figsize=(22, 8))

# Banda fondo segun fase (conservador=azul claro, normal=blanco)
day_starts = day_start_indices + [n_ops]
for i, (idx_start, phase) in enumerate(zip(day_start_indices, day_phases)):
    idx_end = day_starts[i + 1]
    color = "#E3F2FD" if phase == "conservador" else "#FAFAFA"
    ax.axvspan(idx_start, idx_end, alpha=0.35, color=color, zorder=0)

# Linea del balance
ax.plot(x, y, color=LINE_COLOR, linewidth=0.9, alpha=0.75, zorder=2)

# Scatter wins / losses
win_mask  = np.array([c == "win"  for c in op_colors])
loss_mask = np.array([c == "loss" for c in op_colors])

ax.scatter(x[win_mask],  y[win_mask],  color=WIN_COLOR,  s=22, zorder=4, label="Victoria")
ax.scatter(x[loss_mask], y[loss_mask], color=LOSS_COLOR, s=22, zorder=4, label="Derrota / Dia cortado")

# Lineas de cambio de dia
for i, (idx, lbl, phase) in enumerate(zip(day_start_indices, day_labels, day_phases)):
    ax.axvline(idx, color="#90CAF9", linewidth=0.6, linestyle="--", zorder=1)
    if i % 4 == 0:
        ax.text(idx + 0.5, min(y) * 0.9985, lbl,
                fontsize=6, color="#1565C0", rotation=90, va="bottom", ha="left")

# Lineas de referencia
ax.axhline(300, color="#FF9800", linewidth=1.2, linestyle=":", alpha=0.85, label="Capital inicial $300")
ax.axhline(600, color="#9C27B0", linewidth=1.0, linestyle="--", alpha=0.7,
           label="$600 → activa modo normal")

# Marca de corte de dias (circulo naranja grande)
for ci in cut_indices:
    if ci < len(x):
        ax.scatter(x[ci], y[ci], color=CUT_COLOR, s=80, marker="x", zorder=5, linewidths=1.5)

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

# Anotacion del umbral de transicion
crossover = next((i for i, idx in enumerate(day_start_indices) if day_phases[i] == "normal"), None)
if crossover is not None:
    ax.axvline(day_start_indices[crossover], color="#9C27B0", linewidth=1.5, linestyle="-", alpha=0.4, zorder=1)
    ax.text(day_start_indices[crossover] + 1, ax.get_ylim()[1] if y.max() == 0 else y.max() * 0.995,
            "◀ CONSERVADOR | NORMAL ▶",
            fontsize=8, color="#6A1B9A", fontweight="bold")

wins_total  = sum(win_mask)
losses_total = sum(loss_mask)
win_rate = wins_total / n_ops * 100

info_text = (
    f"Ops totales: {n_ops}  |  "
    f"Victorias: {wins_total} ({win_rate:.1f}%)  |  "
    f"Derrotas: {losses_total} ({100-win_rate:.1f}%)  |  "
    f"Dias cortados: {len(cut_days)}  |  "
    f"Balance final: ${y[-1]:,.2f}  |  "
    f"Dias operados: {len(daily_seen)}"
)
fig.text(0.5, 0.01, info_text, ha="center", fontsize=9, color="#444")

ax.set_xlabel("Número de operacion", fontsize=10)
ax.set_ylabel("Balance ($)", fontsize=10)
ax.set_title(
    "Historial operacion a operacion — Regla Conservadora Masaniello 6/2\n"
    "Fondo azul = fase conservadora (<$600): meta $40, corte al 1er loss  |  "
    "Fondo blanco = fase normal (≥$600): meta $60\n"
    "Canales: VIP TRADER A + SMART SIGNALS | 17 Mar → 7 May 2026",
    fontsize=11,
    fontweight="bold",
)
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))

# Leyenda
cut_patch = mpatches.Patch(color=CUT_COLOR, label="Dia cortado (X naranja = ultima op del dia)")
conserv_patch = mpatches.Patch(color="#BBDEFB", alpha=0.6, label="Fase conservadora (<$600)")
normal_patch = mpatches.Patch(color="#F5F5F5", alpha=0.8, label="Fase normal (≥$600)")
ax.legend(
    handles=[
        plt.Line2D([0], [0], color=LINE_COLOR, lw=2, label="Balance"),
        plt.scatter([], [], color=WIN_COLOR, s=22, label="Victoria"),
        plt.scatter([], [], color=LOSS_COLOR, s=22, label="Derrota"),
        plt.scatter([], [], color=CUT_COLOR, s=80, marker="x", label="Corte (1er loss)"),
        plt.Line2D([0], [0], color="#FF9800", lw=1.5, linestyle=":", label="$300 capital inicial"),
        plt.Line2D([0], [0], color="#9C27B0", lw=1.2, linestyle="--", label="$600 umbral"),
        conserv_patch,
        normal_patch,
    ],
    fontsize=8, loc="upper left",
)
ax.grid(True, alpha=0.2, axis="y")
ax.set_xlim(-2, n_ops + 5)

plt.tight_layout(rect=[0, 0.04, 1, 1])

out = ROOT / "runtime" / "grafica_detallada_conservadora.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"Grafica guardada: {out}")
print(f"  Ops totales: {n_ops}")
print(f"  Victorias: {wins_total} ({win_rate:.1f}%)")
print(f"  Derrotas:  {losses_total} ({100-win_rate:.1f}%)")
print(f"  Dias cortados (1er loss): {len(cut_days)}")
print(f"  Balance final: ${y[-1]:,.2f}")
print(f"  Dias operados: {len(daily_seen)}")
