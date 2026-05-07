"""
Grafica del balance diario durante los 2 meses simulados.
Muestra: balance al cierre de cada dia, meta diaria alcanzada o no, y drawdown.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sim_dinero_real_meta60 import simulate_money

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sessions, daily, stats = simulate_money()

days_sorted = sorted(daily.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
dates = [datetime.strptime(d, "%d/%m/%Y") for d in days_sorted]
balances = [daily[d]["end_balance"] for d in days_sorted]
metas = [daily[d]["meta_hit"] for d in days_sorted]
min_balances = [daily[d]["min_balance"] for d in days_sorted]

# --- Figura con 2 subplots ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
fig.suptitle(
    "Simulacion: 2 victorias por sesion, stop al llegar a $60/dia\n"
    "Banca base $300 — Historico real del canal VIP",
    fontsize=13,
    fontweight="bold",
)

# ---- Panel 1: balance diario ---
ax1.plot(dates, balances, color="#2196F3", linewidth=2, label="Balance cierre del dia", zorder=3)
ax1.axhline(300, color="#FF9800", linestyle="--", linewidth=1.2, label="Capital inicial ($300)")
ax1.axhline(600, color="#4CAF50", linestyle="--", linewidth=1.0, alpha=0.7, label="Capital duplicado ($600)")

for i, (date, bal, hit) in enumerate(zip(dates, balances, metas)):
    color = "#4CAF50" if hit else "#F44336"
    ax1.scatter(date, bal, color=color, s=40, zorder=4)

ax1.fill_between(
    dates, [300] * len(dates), balances,
    where=[b >= 300 for b in balances],
    alpha=0.12, color="#2196F3", interpolate=True
)

ax1.set_ylabel("Balance ($)", fontsize=11)
ax1.set_title("Balance acumulado al cierre de cada dia", fontsize=11)
ax1.legend(loc="upper left", fontsize=9)
ax1.grid(True, alpha=0.3)
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))

# Anotar balance final
ax1.annotate(
    f"${stats['final_balance']:,.2f}",
    xy=(dates[-1], balances[-1]),
    xytext=(-60, 10),
    textcoords="offset points",
    fontsize=9,
    color="#2196F3",
    arrowprops=dict(arrowstyle="->", color="#2196F3", lw=1),
)

# ---- Panel 2: ganancia diaria (PnL por dia) ---
pnls = [daily[d]["pnl"] for d in days_sorted]
bar_colors = ["#4CAF50" if p >= 60 else "#FF9800" if p > 0 else "#F44336" for p in pnls]
ax2.bar(dates, pnls, color=bar_colors, width=0.6, zorder=2)
ax2.axhline(60, color="#2196F3", linestyle="--", linewidth=1.2, label="Meta $60")
ax2.axhline(0, color="black", linewidth=0.8)

ax2.set_ylabel("Ganancia del dia ($)", fontsize=11)
ax2.set_title("Ganancia neta por dia", fontsize=11)
ax2.set_xlabel("Fecha", fontsize=11)
ax2.legend(loc="upper left", fontsize=9)
ax2.grid(True, alpha=0.3, axis="y")
ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))

# Leyenda manual puntos
patch_ok = mpatches.Patch(color="#4CAF50", label="Meta alcanzada")
patch_no = mpatches.Patch(color="#F44336", label="Meta NO alcanzada")
patch_par = mpatches.Patch(color="#FF9800", label="Dia parcial")
ax1.legend(
    handles=[
        plt.Line2D([0], [0], color="#2196F3", lw=2, label="Balance"),
        plt.Line2D([0], [0], color="#FF9800", lw=1.2, linestyle="--", label="Capital inicial ($300)"),
        plt.Line2D([0], [0], color="#4CAF50", lw=1.0, linestyle="--", label="Capital duplicado ($600)"),
        patch_ok,
        patch_no,
    ],
    fontsize=8.5,
    loc="upper left",
)

plt.xticks(rotation=45, ha="right", fontsize=8)
plt.tight_layout()

out = ROOT / "runtime" / "grafica_dos_meses.png"
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"Grafica guardada en: {out}")

# Estadisticas adicionales para el titulo
dias_meta = sum(1 for d in days_sorted if daily[d]["meta_hit"])
print(f"  Dias con meta $60: {dias_meta}/{len(days_sorted)}")
print(f"  Balance inicial: $300.00")
print(f"  Balance final:   ${stats['final_balance']:,.2f}")
print(f"  Drawdown maximo: ${stats['max_drawdown']:.2f}")
