"""
Backtest Evolucionado: Split-Debt Recovery (A vs B)

Objetivo:
- Comparar dos escenarios de recuperación de deuda tras una L:
  A) 2 chunks de deuda ($15 c/u), stake recovery = $18.48
  B) 3 chunks de deuda ($10 c/u), stake recovery = $13.04

- Medir supervivencia y riesgo con datos reales de ejemplo.md:
  * ocurrencias de dos L dentro de una ventana de 5 señales
  * drawdown máximo
  * probabilidad de quiebre (Stop Loss Global activado)
  * supervivencia a 24h (ventanas deslizantes)

Salida:
- runtime/split_debt_evolucionado.xlsx (4 pestañas)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "ejemplo.md"
OUTPUT_PATH = ROOT / "runtime" / "split_debt_evolucionado.xlsx"

INITIAL_CAPITAL = 300.0
PAYOUT = 0.92
BASE_ENTRY = 2.0
DEBT_FIXED = 30.0

# Multiplicadores martingala para que cada escalón cubra pérdida previa + profit objetivo
G1_MULT = (1.0 + PAYOUT) / PAYOUT
G2_MULT = G1_MULT * G1_MULT


@dataclass
class Signal:
    idx: int
    ts: datetime
    day: str
    result: str  # WD / G1 / G2 / L


@dataclass
class ScenarioConfig:
    name: str
    chunks: int

    @property
    def chunk_debt(self) -> float:
        return DEBT_FIXED / self.chunks

    @property
    def recovery_target(self) -> float:
        # amortización de chunk + beneficio base
        return self.chunk_debt + BASE_ENTRY

    @property
    def recovery_entry(self) -> float:
        return self.recovery_target / PAYOUT


@dataclass
class RunResult:
    scenario: str
    chunks: int
    recovery_entry: float
    final_balance: float
    pnl_total: float
    roi_pct: float
    max_dd: float
    stop_global_count: int
    stopped: bool
    stop_signal_idx: int
    processed_signals: int
    total_signals: int
    skipped_signals: int
    l_events: int
    recoveries_completed: int
    recoveries_incomplete: int
    equity_rows: list[dict]
    daily_rows: list[dict]


def parse_signals(path: Path) -> list[Signal]:
    date_pat = re.compile(r"^\[(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}:\d{2})\]")
    res_pat = re.compile(
        r"(VICTORIA DIRECTA|VICTORIA EN 1.*?MARTINGALA|VICTORIA EN 2.*?MARTINGALA|P[EÉ]RDIDA)",
        re.IGNORECASE,
    )
    map_res = {
        "victoria directa": "WD",
        "victoria en 1": "G1",
        "victoria en 2": "G2",
        "perdida": "L",
        "pérdida": "L",
    }

    out: list[Signal] = []
    idx = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        dm = date_pat.match(line)
        if not dm:
            continue
        rm = res_pat.search(line)
        if not rm:
            continue

        ts = datetime.strptime(" ".join(dm.groups()), "%d/%m/%Y %H:%M:%S")
        raw = rm.group(1).lower()
        result = next((v for k, v in map_res.items() if raw.startswith(k)), None)
        if result is None:
            continue
        idx += 1
        out.append(Signal(idx=idx, ts=ts, day=ts.strftime("%d/%m/%Y"), result=result))

    out.sort(key=lambda s: s.ts)
    return out


def pnl_for(result: str, entry: float) -> float:
    g1 = entry * G1_MULT
    g2 = g1 * G1_MULT
    if result == "WD":
        return round(entry * PAYOUT, 4)
    if result == "G1":
        return round(g1 * PAYOUT - entry, 4)
    if result == "G2":
        return round(g2 * PAYOUT - entry - g1, 4)
    return round(-(entry + g1 + g2), 4)


def run_scenario(signals: list[Signal], cfg: ScenarioConfig, capital: float = INITIAL_CAPITAL) -> RunResult:
    balance = float(capital)
    peak = float(capital)
    max_dd = 0.0

    phase: Literal["standard", "recovery", "stopped"] = "standard"
    pending_chunks = 0

    stop_count = 0
    stop_signal_idx = 0

    l_events = 0
    rec_completed = 0

    equity_rows: list[dict] = []
    daily_map: dict[str, list[float]] = {}

    for s in signals:
        if phase == "stopped":
            break

        if phase == "standard":
            entry = BASE_ENTRY
            pnl = pnl_for(s.result, entry)
            balance = round(balance + pnl, 4)

            equity_rows.append({
                "Signal": s.idx,
                "Fecha": s.day,
                "Resultado": s.result,
                "Fase": "Estandar",
                "Entrada": round(entry, 4),
                "PnL": round(pnl, 4),
                "Balance": round(balance, 4),
            })
            daily_map.setdefault(s.day, []).append(balance)

            if s.result == "L":
                l_events += 1
                phase = "recovery"
                pending_chunks = cfg.chunks

        else:  # recovery
            entry = cfg.recovery_entry
            pnl = pnl_for(s.result, entry)
            balance = round(balance + pnl, 4)

            equity_rows.append({
                "Signal": s.idx,
                "Fecha": s.day,
                "Resultado": s.result,
                "Fase": "Recuperacion",
                "Entrada": round(entry, 4),
                "PnL": round(pnl, 4),
                "Balance": round(balance, 4),
            })
            daily_map.setdefault(s.day, []).append(balance)

            if s.result == "L":
                # segunda L con deuda pendiente => quiebre operativo
                stop_count += 1
                stop_signal_idx = s.idx
                phase = "stopped"
            else:
                pending_chunks -= 1
                if pending_chunks <= 0:
                    rec_completed += 1
                    phase = "standard"

        peak = max(peak, balance)
        max_dd = max(max_dd, round(peak - balance, 4))

        if balance <= 0 and phase != "stopped":
            # protección adicional: quiebra por balance
            stop_count += 1
            stop_signal_idx = s.idx
            phase = "stopped"

    processed = len(equity_rows)
    total = len(signals)
    skipped = total - processed

    sorted_days = sorted(daily_map.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
    daily_rows: list[dict] = []
    prev = capital
    for day in sorted_days:
        close_bal = round(daily_map[day][-1], 2)
        pnl_day = round(close_bal - prev, 2)
        daily_rows.append({
            "Fecha": day,
            "Bal Apertura": round(prev, 2),
            "Bal Cierre": close_bal,
            "PnL Diario": pnl_day,
        })
        prev = close_bal

    final_balance = round(balance, 2)
    pnl_total = round(final_balance - capital, 2)
    roi = round((pnl_total / capital) * 100, 2)

    return RunResult(
        scenario=cfg.name,
        chunks=cfg.chunks,
        recovery_entry=round(cfg.recovery_entry, 4),
        final_balance=final_balance,
        pnl_total=pnl_total,
        roi_pct=roi,
        max_dd=round(max_dd, 2),
        stop_global_count=stop_count,
        stopped=(stop_count > 0),
        stop_signal_idx=stop_signal_idx,
        processed_signals=processed,
        total_signals=total,
        skipped_signals=skipped,
        l_events=l_events,
        recoveries_completed=rec_completed,
        recoveries_incomplete=max(0, l_events - rec_completed - stop_count),
        equity_rows=equity_rows,
        daily_rows=daily_rows,
    )


def count_double_l_in_5(signals: list[Signal]) -> tuple[int, float, list[dict]]:
    """
    Cuenta ventanas de 5 señales que tienen >=2 pérdidas L.
    Devuelve total ventanas, porcentaje y ejemplos.
    """
    n = len(signals)
    if n < 5:
        return 0, 0.0, []

    hits = 0
    samples: list[dict] = []
    total_windows = n - 5 + 1

    for i in range(total_windows):
        win = signals[i:i + 5]
        l_count = sum(1 for s in win if s.result == "L")
        if l_count >= 2:
            hits += 1
            if len(samples) < 25:
                samples.append({
                    "Desde señal": win[0].idx,
                    "Hasta señal": win[-1].idx,
                    "Fecha inicio": win[0].day,
                    "Fecha fin": win[-1].day,
                    "L en ventana": l_count,
                    "Secuencia": "-".join(s.result for s in win),
                })

    pct = round(hits / total_windows * 100, 2)
    return hits, pct, samples


def simulate_24h_survival(signals: list[Signal], cfg: ScenarioConfig) -> tuple[float, int, int]:
    """
    Supervivencia a 24h en ventanas deslizantes ancladas en cada señal.
    """
    survive = 0
    total = 0

    for i, start_sig in enumerate(signals):
        end_ts = start_sig.ts + timedelta(hours=24)
        window: list[Signal] = []
        for s in signals[i:]:
            if s.ts <= end_ts:
                window.append(s)
            else:
                break
        if not window:
            continue

        total += 1
        rr = run_scenario(window, cfg, capital=INITIAL_CAPITAL)
        if not rr.stopped and rr.final_balance > 0:
            survive += 1

    pct = round((survive / total * 100) if total else 0.0, 2)
    return pct, survive, total


def build_report(
    signals: list[Signal],
    a: RunResult,
    b: RunResult,
    l5_hits: int,
    l5_pct: float,
    l5_samples: list[dict],
    surv_a: tuple[float, int, int],
    surv_b: tuple[float, int, int],
) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(OUTPUT_PATH, engine="xlsxwriter") as writer:
        wb = writer.book

        def fmt(**kw):
            return wb.add_format({"border": 1, "align": "center", "valign": "vcenter", **kw})

        f_title = fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", font_size=13)
        f_hdr_a = fmt(bold=True, font_color="#FFFFFF", bg_color="#8B1A1A")
        f_hdr_b = fmt(bold=True, font_color="#FFFFFF", bg_color="#1E6F4B")
        f_hdr_n = fmt(bold=True, font_color="#FFFFFF", bg_color="#2E75B6")

        # Hoja 1: Resumen comparativo
        ws1 = wb.add_worksheet("Resumen")
        writer.sheets["Resumen"] = ws1
        ws1.hide_gridlines(2)
        ws1.set_column("A:A", 34)
        ws1.set_column("B:D", 20)

        ws1.merge_range("A1:D1", "Split-Debt Recovery Evolucionado (A vs B)", f_title)
        ws1.merge_range("A2:D2", "Capital $300 | Payout 92% | Base $2 | Martingala G1-G2", f_hdr_n)

        ws1.write(3, 0, "KPI", f_hdr_n)
        ws1.write(3, 1, "Escenario A (2 chunks)", f_hdr_a)
        ws1.write(3, 2, "Escenario B (3 chunks)", f_hdr_b)
        ws1.write(3, 3, "Delta (B-A)", f_hdr_n)

        rows = [
            ("Stake recuperación", a.recovery_entry, b.recovery_entry, "money"),
            ("Balance final", a.final_balance, b.final_balance, "money"),
            ("P&L total", a.pnl_total, b.pnl_total, "money"),
            ("ROI (%)", a.roi_pct, b.roi_pct, "pct"),
            ("Drawdown máximo", a.max_dd, b.max_dd, "money"),
            ("Stop Loss Globales", a.stop_global_count, b.stop_global_count, "int"),
            ("Señal de stop", a.stop_signal_idx, b.stop_signal_idx, "int"),
            ("Señales procesadas", a.processed_signals, b.processed_signals, "int"),
            ("Recuperaciones completadas", a.recoveries_completed, b.recoveries_completed, "int"),
            ("Recuperaciones incompletas", a.recoveries_incomplete, b.recoveries_incomplete, "int"),
            ("Supervivencia 24h (%)", surv_a[0], surv_b[0], "pct"),
        ]

        for i, (k, va, vb, tp) in enumerate(rows, start=4):
            even = i % 2 == 0
            bg = "F7FBFF" if even else "FFFFFF"
            ws1.write(i, 0, k, fmt(bg_color=bg, bold=True, align="left"))

            if tp == "money":
                ws1.write_number(i, 1, float(va), fmt(bg_color=bg, num_format="$#,##0.00"))
                ws1.write_number(i, 2, float(vb), fmt(bg_color=bg, num_format="$#,##0.00"))
                diff = round(float(vb) - float(va), 2)
                ws1.write_number(
                    i, 3, diff,
                    fmt(bg_color=bg, num_format="$#,##0.00", bold=True,
                        font_color="#1E6F4B" if diff >= 0 else "#8B1A1A"),
                )
            elif tp == "pct":
                ws1.write_number(i, 1, float(va), fmt(bg_color=bg, num_format='0.00"%"'))
                ws1.write_number(i, 2, float(vb), fmt(bg_color=bg, num_format='0.00"%"'))
                diff = round(float(vb) - float(va), 2)
                ws1.write_number(
                    i, 3, diff,
                    fmt(bg_color=bg, num_format='0.00"%"', bold=True,
                        font_color="#1E6F4B" if diff >= 0 else "#8B1A1A"),
                )
            else:
                ws1.write_number(i, 1, int(va), fmt(bg_color=bg))
                ws1.write_number(i, 2, int(vb), fmt(bg_color=bg))
                diff = int(vb) - int(va)
                ws1.write_number(
                    i, 3, diff,
                    fmt(bg_color=bg, bold=True,
                        font_color="#1E6F4B" if diff >= 0 else "#8B1A1A"),
                )

        base_row = len(rows) + 6
        ws1.merge_range(base_row, 0, base_row, 3, "Analisis de Supervivencia (Riesgo L-L)", f_hdr_n)
        ws1.write(base_row + 1, 0, "Ventanas de 5 señales con >=2 L", fmt(bold=True, align="left"))
        ws1.write_number(base_row + 1, 1, l5_hits)
        ws1.write(base_row + 2, 0, "% de ventanas con >=2 L", fmt(bold=True, align="left"))
        ws1.write_number(base_row + 2, 1, l5_pct, fmt(num_format='0.00"%"'))
        ws1.write(base_row + 3, 0, "Supervivencia 24h A (ok/total)", fmt(bold=True, align="left"))
        ws1.write(base_row + 3, 1, f"{surv_a[1]}/{surv_a[2]}")
        ws1.write(base_row + 4, 0, "Supervivencia 24h B (ok/total)", fmt(bold=True, align="left"))
        ws1.write(base_row + 4, 1, f"{surv_b[1]}/{surv_b[2]}")

        # Hoja 2: L-L en 5 señales
        df_l5 = pd.DataFrame(l5_samples)
        if df_l5.empty:
            df_l5 = pd.DataFrame([{
                "Desde señal": 0,
                "Hasta señal": 0,
                "Fecha inicio": "-",
                "Fecha fin": "-",
                "L en ventana": 0,
                "Secuencia": "Sin eventos",
            }])
        df_l5.to_excel(writer, sheet_name="LL_en_5_senales", index=False, startrow=2)
        ws2 = writer.sheets["LL_en_5_senales"]
        ws2.hide_gridlines(2)
        ws2.merge_range("A1:F1", "Eventos de Riesgo: 2 o mas L en 5 señales", f_title)
        ws2.set_column("A:F", 20)
        for c, h in enumerate(df_l5.columns):
            ws2.write(2, c, h, f_hdr_n)

        # Hoja 3: Diario comparativo
        dfa = pd.DataFrame(a.daily_rows)
        dfb = pd.DataFrame(b.daily_rows)
        dfd = pd.merge(dfa, dfb, on="Fecha", suffixes=(" A", " B"))
        dfd.to_excel(writer, sheet_name="Diario", index=False, startrow=2)

        ws3 = writer.sheets["Diario"]
        ws3.hide_gridlines(2)
        ws3.merge_range("A1:G1", "Comparativo Diario A vs B", f_title)
        ws3.set_column("A:A", 14)
        ws3.set_column("B:G", 16)
        headers = ["Fecha", "Bal Aper A", "Bal Cierre A", "PnL A", "Bal Aper B", "Bal Cierre B", "PnL B"]
        colors = ["#2E75B6", "#8B1A1A", "#8B1A1A", "#8B1A1A", "#1E6F4B", "#1E6F4B", "#1E6F4B"]
        for c, (h, col) in enumerate(zip(headers, colors)):
            ws3.write(2, c, h, fmt(bold=True, font_color="#FFFFFF", bg_color=col))

        # Hoja 4: Equidad y graficas
        ws4 = wb.add_worksheet("Equidad")
        writer.sheets["Equidad"] = ws4
        ws4.hide_gridlines(2)
        ws4.set_column("A:I", 16)
        ws4.merge_range("A1:I1", "Curva de Equidad y Valles de Recuperacion", f_title)

        # preparar serie por señal
        # toma largo maximo para unir ambas curvas
        max_n = max(len(a.equity_rows), len(b.equity_rows))
        ws4.write_row(2, 0, ["Signal", "Balance A", "Balance B", "Valle A", "Valle B", "Fecha"], f_hdr_n)

        by_idx_a = {r["Signal"]: r for r in a.equity_rows}
        by_idx_b = {r["Signal"]: r for r in b.equity_rows}

        for i in range(1, max_n + 1):
            ra = by_idx_a.get(i)
            rb = by_idx_b.get(i)
            row = i + 2
            ws4.write_number(row, 0, i)
            if ra:
                ws4.write_number(row, 1, float(ra["Balance"]))
                ws4.write(row, 5, ra["Fecha"])
                if ra["Resultado"] == "L":
                    ws4.write_number(row, 3, float(ra["Balance"]))
            if rb:
                ws4.write_number(row, 2, float(rb["Balance"]))
                if rb["Resultado"] == "L":
                    ws4.write_number(row, 4, float(rb["Balance"]))

        last_row = max_n + 2

        chart = wb.add_chart({"type": "line"})
        chart.add_series({
            "name": "Escenario A (2 chunks)",
            "categories": ["Equidad", 3, 0, last_row, 0],
            "values": ["Equidad", 3, 1, last_row, 1],
            "line": {"color": "#8B1A1A", "width": 2.0},
        })
        chart.add_series({
            "name": "Escenario B (3 chunks)",
            "categories": ["Equidad", 3, 0, last_row, 0],
            "values": ["Equidad", 3, 2, last_row, 2],
            "line": {"color": "#1E6F4B", "width": 2.2},
        })
        chart.add_series({
            "name": "Valles A",
            "categories": ["Equidad", 3, 0, last_row, 0],
            "values": ["Equidad", 3, 3, last_row, 3],
            "line": {"none": True},
            "marker": {
                "type": "diamond",
                "size": 7,
                "fill": {"color": "#FF0000"},
                "border": {"color": "#8B1A1A"},
            },
        })
        chart.add_series({
            "name": "Valles B",
            "categories": ["Equidad", 3, 0, last_row, 0],
            "values": ["Equidad", 3, 4, last_row, 4],
            "line": {"none": True},
            "marker": {
                "type": "circle",
                "size": 6,
                "fill": {"color": "#FFA500"},
                "border": {"color": "#E26B0A"},
            },
        })
        chart.set_title({"name": "Equidad: valles y recuperacion gradual"})
        chart.set_x_axis({"name": "Signal"})
        chart.set_y_axis({"name": "Balance USD"})
        chart.set_legend({"position": "bottom"})
        ws4.insert_chart("K2", chart, {"x_scale": 2.0, "y_scale": 1.6})


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"No se encontró {INPUT_PATH}")

    signals = parse_signals(INPUT_PATH)
    print(f"Señales cargadas: {len(signals)}")

    cfg_a = ScenarioConfig(name="Escenario A", chunks=2)
    cfg_b = ScenarioConfig(name="Escenario B", chunks=3)

    run_a = run_scenario(signals, cfg_a)
    run_b = run_scenario(signals, cfg_b)

    l5_hits, l5_pct, l5_samples = count_double_l_in_5(signals)

    surv_a = simulate_24h_survival(signals, cfg_a)
    surv_b = simulate_24h_survival(signals, cfg_b)

    print()
    print("=" * 68)
    print(f"{'KPI':<36} {'A (2 chunks)':>14} {'B (3 chunks)':>14}")
    print("=" * 68)
    print(f"{'Stake recovery':<36} {run_a.recovery_entry:>14.4f} {run_b.recovery_entry:>14.4f}")
    print(f"{'Balance final ($)':<36} {run_a.final_balance:>14.2f} {run_b.final_balance:>14.2f}")
    print(f"{'P&L total ($)':<36} {run_a.pnl_total:>14.2f} {run_b.pnl_total:>14.2f}")
    print(f"{'ROI (%)':<36} {run_a.roi_pct:>14.2f} {run_b.roi_pct:>14.2f}")
    print(f"{'Drawdown max ($)':<36} {run_a.max_dd:>14.2f} {run_b.max_dd:>14.2f}")
    print(f"{'Stop Loss Globales':<36} {run_a.stop_global_count:>14} {run_b.stop_global_count:>14}")
    print(f"{'Supervivencia 24h (%)':<36} {surv_a[0]:>14.2f} {surv_b[0]:>14.2f}")
    print("=" * 68)
    print(f"Ventanas de 5 señales con >=2L: {l5_hits} ({l5_pct:.2f}%)")

    build_report(signals, run_a, run_b, l5_hits, l5_pct, l5_samples, surv_a, surv_b)
    print(f"Reporte guardado en: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
