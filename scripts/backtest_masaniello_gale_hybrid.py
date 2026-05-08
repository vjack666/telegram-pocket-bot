"""
Backtest: Masaniello-Gale Hybrid (micro + macro)

Nivel 1 (micro): Masaniello por sesion de 6 senales.
Nivel 2 (macro): Gale por sesion (10 -> 20 -> 40 -> ... en cada dia).

Escenarios comparados:
- Agresivo: 3 ITM de 6
- Conservador: 2 ITM de 6

Input: ejemplo.md
Output: runtime/backtest_masaniello_gale_hybrid.xlsx
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "ejemplo.md"
OUTPUT_PATH = ROOT / "runtime" / "backtest_masaniello_gale_hybrid.xlsx"

INITIAL_CAPITAL = 300.0
PAYOUT = 0.92
BASE_SESSION_STAKE = 10.0
META_DIARIA = 50.0
OPS_OBJETIVO = 6


@dataclass
class Signal:
    ts: datetime
    day: str
    wl: str  # W/L


def parse_signals(path: Path) -> list[Signal]:
    date_pat = re.compile(r"^\[(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}:\d{2})\]")
    res_pat = re.compile(
        r"(VICTORIA DIRECTA|VICTORIA EN 1.*?MARTINGALA|VICTORIA EN 2.*?MARTINGALA|P[EÉ]RDIDA)",
        re.IGNORECASE,
    )
    out: list[Signal] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        dm = date_pat.match(line)
        if not dm:
            continue
        rm = res_pat.search(line)
        if not rm:
            continue
        ts = datetime.strptime(" ".join(dm.groups()), "%d/%m/%Y %H:%M:%S")
        raw = rm.group(1).lower()
        wl = "L" if ("perdida" in raw or "pérdida" in raw) else "W"
        out.append(Signal(ts=ts, day=ts.strftime("%d/%m/%Y"), wl=wl))
    out.sort(key=lambda s: s.ts)
    return out


def calcular_stake_masaniello(
    capital_sesion: float,
    total_ops: int,
    itm_requeridos: int,
    ops_realizadas: int,
    itms_logrados: int,
    payout: float = PAYOUT,
) -> float:
    """Version simplificada/estable para stake dinamico intrasesion."""
    restantes = total_ops - ops_realizadas
    faltantes = itm_requeridos - itms_logrados

    if faltantes <= 0:
        return 0.0
    if faltantes > restantes:
        return 0.0

    p_inv = faltantes / restantes
    denom = 1.0 + payout * (1.0 - p_inv)
    if denom <= 0:
        return 0.0

    stake = (capital_sesion * p_inv) / denom
    return round(max(stake, 1.0), 2)


def simular_sesion_hibrida(
    señales_bloque: list[str],
    cap_inicial_sesion: float,
    itm_objetivo: int,
    ops_objetivo: int = OPS_OBJETIVO,
) -> tuple[bool, float, int, int, int, list[dict]]:
    """
    Ejecuta una sesion micro de hasta 6 senales.
    Return: exito, balance_final_sesion, ops_usadas, itms, otms, detalle
    """
    balance_sesion = cap_inicial_sesion
    itms = 0
    otms = 0
    detalle: list[dict] = []

    for i in range(min(ops_objetivo, len(señales_bloque))):
        stake = calcular_stake_masaniello(
            cap_inicial_sesion,
            ops_objetivo,
            itm_objetivo,
            i,
            itms,
            PAYOUT,
        )

        if stake <= 0:
            # sesion matematicamente cerrada
            break

        resultado = señales_bloque[i]
        if resultado == "W":
            itms += 1
            pnl = round(stake * PAYOUT, 2)
            balance_sesion = round(balance_sesion + pnl, 2)
        else:
            otms += 1
            pnl = round(-stake, 2)
            balance_sesion = round(balance_sesion + pnl, 2)

        detalle.append(
            {
                "Op": i + 1,
                "Resultado": resultado,
                "Stake": stake,
                "PnL": pnl,
                "Balance Sesion": balance_sesion,
            }
        )

        # objetivo alcanzado
        if itms >= itm_objetivo:
            return True, balance_sesion, i + 1, itms, otms, detalle

        # ya no hay forma de llegar al objetivo
        if (ops_objetivo - i - 1) < (itm_objetivo - itms):
            return False, 0.0, i + 1, itms, otms, detalle

    exito = itms >= itm_objetivo
    return (exito, balance_sesion if exito else 0.0, len(detalle), itms, otms, detalle)


def run_backtest(
    signals: list[Signal],
    itm_objetivo: int,
    scenario_name: str,
    meta_diaria: float = META_DIARIA,
    base_session_stake: float = BASE_SESSION_STAKE,
) -> dict:
    # agrupar por dia
    day_map: dict[str, list[str]] = {}
    for s in signals:
        day_map.setdefault(s.day, []).append(s.wl)

    ordered_days = sorted(day_map.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))

    capital_total = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0.0

    daily_rows: list[dict] = []
    sessions_rows: list[dict] = []
    equity_rows: list[dict] = []

    total_sessions = 0
    won_sessions = 0
    lost_sessions = 0
    survival_days = 0
    ruin_day = ""

    for day in ordered_days:
        if capital_total < base_session_stake:
            ruin_day = day
            break

        day_start = capital_total
        day_signals = day_map[day]
        idx = 0

        ganancia_dia = 0.0
        stake_sesion = base_session_stake
        sessions_today = 0
        meta_hit = False

        # consumir bloques de 6 mientras haya señales y no se cumpla meta
        while (idx + OPS_OBJETIVO) <= len(day_signals) and ganancia_dia < meta_diaria:
            if capital_total < stake_sesion:
                ruin_day = day
                break

            bloque = day_signals[idx: idx + OPS_OBJETIVO]
            idx += OPS_OBJETIVO
            sessions_today += 1
            total_sessions += 1

            exito, final_sesion, ops_usadas, itms, otms, detalle = simular_sesion_hibrida(
                bloque,
                cap_inicial_sesion=stake_sesion,
                itm_objetivo=itm_objetivo,
                ops_objetivo=OPS_OBJETIVO,
            )

            if exito:
                utilidad = round(final_sesion - stake_sesion, 2)
                ganancia_dia = round(ganancia_dia + utilidad, 2)
                capital_total = round(capital_total + utilidad, 2)
                won_sessions += 1
                session_status = "WIN"
                next_stake = base_session_stake
            else:
                perdida = round(stake_sesion, 2)
                ganancia_dia = round(ganancia_dia - perdida, 2)
                capital_total = round(capital_total - perdida, 2)
                lost_sessions += 1
                session_status = "LOSS"
                next_stake = round(stake_sesion * 2.0, 2)

            peak = max(peak, capital_total)
            max_dd = max(max_dd, round(peak - capital_total, 2))

            sessions_rows.append(
                {
                    "Escenario": scenario_name,
                    "Fecha": day,
                    "Sesion #": sessions_today,
                    "Stake Sesion": stake_sesion,
                    "Estado": session_status,
                    "Ops usadas": ops_usadas,
                    "ITM": itms,
                    "OTM": otms,
                    "Resultado bloque": "".join(bloque),
                    "Ganancia Dia Acum": ganancia_dia,
                    "Capital Total": capital_total,
                    "Detalle Ops": " | ".join(
                        f"{d['Op']}:{d['Resultado']}@{d['Stake']}({d['PnL']:+.2f})" for d in detalle
                    ),
                }
            )

            equity_rows.append(
                {
                    "Escenario": scenario_name,
                    "Fecha": day,
                    "Paso": len(equity_rows) + 1,
                    "Capital": capital_total,
                    "Ganancia Dia": ganancia_dia,
                    "Sesion #": sessions_today,
                }
            )

            stake_sesion = next_stake
            if ganancia_dia >= meta_diaria:
                meta_hit = True

        day_end = capital_total
        daily_rows.append(
            {
                "Escenario": scenario_name,
                "Fecha": day,
                "Capital Inicio": day_start,
                "Capital Cierre": day_end,
                "PnL Dia": round(day_end - day_start, 2),
                "Ganancia Dia (modelo)": ganancia_dia,
                "Sesiones": sessions_today,
                "Meta 50 alcanzada": "SI" if meta_hit else "NO",
                "Senales consumidas": idx,
                "Senales sin usar": len(day_signals) - idx,
            }
        )

        if ruin_day:
            break
        survival_days += 1

    status = "Ruin" if ruin_day else "Completado"
    final_capital = round(capital_total, 2)
    pnl_total = round(final_capital - INITIAL_CAPITAL, 2)
    roi = round((pnl_total / INITIAL_CAPITAL) * 100, 2)

    return {
        "scenario": scenario_name,
        "itm_objetivo": itm_objetivo,
        "final_capital": final_capital,
        "pnl_total": pnl_total,
        "roi": roi,
        "max_dd": max_dd,
        "total_sessions": total_sessions,
        "won_sessions": won_sessions,
        "lost_sessions": lost_sessions,
        "winrate_sessions": round((won_sessions / total_sessions * 100), 2) if total_sessions else 0.0,
        "survival_days": survival_days,
        "ruin_day": ruin_day or "No",
        "status": status,
        "daily_rows": daily_rows,
        "sessions_rows": sessions_rows,
        "equity_rows": equity_rows,
    }


def build_excel(res_a: dict, res_b: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    df_daily_a = pd.DataFrame(res_a["daily_rows"])
    df_daily_b = pd.DataFrame(res_b["daily_rows"])
    df_sessions = pd.concat(
        [pd.DataFrame(res_a["sessions_rows"]), pd.DataFrame(res_b["sessions_rows"])],
        ignore_index=True,
    )

    comp_daily = pd.merge(
        df_daily_a[["Fecha", "Capital Inicio", "Capital Cierre", "PnL Dia", "Meta 50 alcanzada", "Sesiones"]],
        df_daily_b[["Fecha", "Capital Inicio", "Capital Cierre", "PnL Dia", "Meta 50 alcanzada", "Sesiones"]],
        on="Fecha",
        suffixes=(" A(3/6)", " B(2/6)"),
    )

    with pd.ExcelWriter(OUTPUT_PATH, engine="xlsxwriter") as writer:
        wb = writer.book

        def fmt(**kw):
            return wb.add_format({"border": 1, "align": "center", "valign": "vcenter", **kw})

        f_title = fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", font_size=13)
        f_hdr = fmt(bold=True, font_color="#FFFFFF", bg_color="#2E75B6")
        f_a = fmt(bold=True, font_color="#FFFFFF", bg_color="#8B1A1A")
        f_b = fmt(bold=True, font_color="#FFFFFF", bg_color="#1E6F4B")

        # Hoja 1 resumen
        ws1 = wb.add_worksheet("Resumen")
        writer.sheets["Resumen"] = ws1
        ws1.hide_gridlines(2)
        ws1.set_column("A:A", 33)
        ws1.set_column("B:D", 20)

        ws1.merge_range("A1:D1", "Masaniello-Gale Hybrid: 3/6 vs 2/6", f_title)
        ws1.merge_range("A2:D2", "Capital 300 | Stake base sesion 10 | Meta diaria 50 | Bloques de 6", f_hdr)

        ws1.write(3, 0, "KPI", f_hdr)
        ws1.write(3, 1, "A (3/6)", f_a)
        ws1.write(3, 2, "B (2/6)", f_b)
        ws1.write(3, 3, "Delta B-A", f_hdr)

        rows = [
            ("Capital final", res_a["final_capital"], res_b["final_capital"], "money"),
            ("P&L total", res_a["pnl_total"], res_b["pnl_total"], "money"),
            ("ROI (%)", res_a["roi"], res_b["roi"], "pct"),
            ("Drawdown max", res_a["max_dd"], res_b["max_dd"], "money"),
            ("Dias de supervivencia", res_a["survival_days"], res_b["survival_days"], "int"),
            ("Ruin day (si aplica)", res_a["ruin_day"], res_b["ruin_day"], "text"),
            ("Sesiones totales", res_a["total_sessions"], res_b["total_sessions"], "int"),
            ("Sesiones ganadas", res_a["won_sessions"], res_b["won_sessions"], "int"),
            ("Sesiones perdidas", res_a["lost_sessions"], res_b["lost_sessions"], "int"),
            ("Winrate sesiones (%)", res_a["winrate_sessions"], res_b["winrate_sessions"], "pct"),
            ("Estado final", res_a["status"], res_b["status"], "text"),
        ]

        for i, (k, va, vb, tp) in enumerate(rows, start=4):
            bg = "F7FBFF" if i % 2 == 0 else "FFFFFF"
            ws1.write(i, 0, k, fmt(bg_color=bg, bold=True, align="left"))
            if tp == "money":
                ws1.write_number(i, 1, float(va), fmt(bg_color=bg, num_format="$#,##0.00"))
                ws1.write_number(i, 2, float(vb), fmt(bg_color=bg, num_format="$#,##0.00"))
                d = round(float(vb) - float(va), 2)
                ws1.write_number(i, 3, d, fmt(bg_color=bg, num_format="$#,##0.00", bold=True,
                                              font_color="#1E6F4B" if d >= 0 else "#8B1A1A"))
            elif tp == "pct":
                ws1.write_number(i, 1, float(va), fmt(bg_color=bg, num_format='0.00"%"'))
                ws1.write_number(i, 2, float(vb), fmt(bg_color=bg, num_format='0.00"%"'))
                d = round(float(vb) - float(va), 2)
                ws1.write_number(i, 3, d, fmt(bg_color=bg, num_format='0.00"%"', bold=True,
                                              font_color="#1E6F4B" if d >= 0 else "#8B1A1A"))
            elif tp == "int":
                ws1.write_number(i, 1, int(va), fmt(bg_color=bg))
                ws1.write_number(i, 2, int(vb), fmt(bg_color=bg))
                ws1.write_number(i, 3, int(vb) - int(va), fmt(bg_color=bg, bold=True,
                                                              font_color="#1E6F4B" if int(vb) - int(va) >= 0 else "#8B1A1A"))
            else:
                ws1.write(i, 1, str(va), fmt(bg_color=bg))
                ws1.write(i, 2, str(vb), fmt(bg_color=bg))
                ws1.write(i, 3, "-", fmt(bg_color=bg))

        # Hoja 2 diario
        comp_daily.to_excel(writer, sheet_name="Diario", index=False, startrow=2)
        ws2 = writer.sheets["Diario"]
        ws2.hide_gridlines(2)
        ws2.merge_range("A1:K1", "Comparativo Diario", f_title)
        ws2.set_column("A:A", 13)
        ws2.set_column("B:K", 17)
        for c, h in enumerate(comp_daily.columns):
            ws2.write(2, c, h, f_hdr)

        # Hoja 3 sesiones
        df_sessions.to_excel(writer, sheet_name="Sesiones", index=False, startrow=2)
        ws3 = writer.sheets["Sesiones"]
        ws3.hide_gridlines(2)
        ws3.merge_range("A1:K1", "Detalle de Sesiones (micro+macro)", f_title)
        ws3.set_column("A:A", 11)
        ws3.set_column("B:B", 13)
        ws3.set_column("C:C", 9)
        ws3.set_column("D:D", 13)
        ws3.set_column("E:E", 10)
        ws3.set_column("F:H", 10)
        ws3.set_column("I:I", 17)
        ws3.set_column("J:K", 16)
        ws3.set_column("L:L", 60)
        for c, h in enumerate(df_sessions.columns):
            ws3.write(2, c, h, f_hdr)

        # Hoja 4 equidad
        eq_a = pd.DataFrame(res_a["equity_rows"])
        eq_b = pd.DataFrame(res_b["equity_rows"])
        max_n = max(len(eq_a), len(eq_b))

        ws4 = wb.add_worksheet("Equidad")
        writer.sheets["Equidad"] = ws4
        ws4.hide_gridlines(2)
        ws4.set_column("A:F", 16)
        ws4.merge_range("A1:F1", "Curva de Capital", f_title)
        ws4.write_row(2, 0, ["Paso", "Capital A(3/6)", "Capital B(2/6)", "Fecha A", "Fecha B"], f_hdr)

        for i in range(max_n):
            r = i + 3
            ws4.write_number(r, 0, i + 1)
            if i < len(eq_a):
                ws4.write_number(r, 1, float(eq_a.iloc[i]["Capital"]))
                ws4.write(r, 3, str(eq_a.iloc[i]["Fecha"]))
            if i < len(eq_b):
                ws4.write_number(r, 2, float(eq_b.iloc[i]["Capital"]))
                ws4.write(r, 4, str(eq_b.iloc[i]["Fecha"]))

        last = max_n + 2
        chart = wb.add_chart({"type": "line"})
        chart.add_series({
            "name": "A (3/6)",
            "categories": ["Equidad", 3, 0, last, 0],
            "values": ["Equidad", 3, 1, last, 1],
            "line": {"color": "#8B1A1A", "width": 2.0},
        })
        chart.add_series({
            "name": "B (2/6)",
            "categories": ["Equidad", 3, 0, last, 0],
            "values": ["Equidad", 3, 2, last, 2],
            "line": {"color": "#1E6F4B", "width": 2.2},
        })
        chart.set_title({"name": "Equidad comparativa"})
        chart.set_x_axis({"name": "Paso de sesion"})
        chart.set_y_axis({"name": "Capital"})
        chart.set_legend({"position": "bottom"})
        ws4.insert_chart("H2", chart, {"x_scale": 2.0, "y_scale": 1.6})


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"No se encontro {INPUT_PATH}")

    signals = parse_signals(INPUT_PATH)
    print(f"Senales parseadas: {len(signals)}")

    res_a = run_backtest(signals, itm_objetivo=3, scenario_name="A_3de6")
    res_b = run_backtest(signals, itm_objetivo=2, scenario_name="B_2de6")

    print()
    print("=" * 70)
    print(f"{'KPI':<28} {'A (3/6)':>18} {'B (2/6)':>18}")
    print("=" * 70)
    print(f"{'Capital final':<28} {res_a['final_capital']:>18.2f} {res_b['final_capital']:>18.2f}")
    print(f"{'P&L total':<28} {res_a['pnl_total']:>18.2f} {res_b['pnl_total']:>18.2f}")
    print(f"{'ROI (%)':<28} {res_a['roi']:>18.2f} {res_b['roi']:>18.2f}")
    print(f"{'Drawdown max':<28} {res_a['max_dd']:>18.2f} {res_b['max_dd']:>18.2f}")
    print(f"{'Dias supervivencia':<28} {res_a['survival_days']:>18} {res_b['survival_days']:>18}")
    print(f"{'Ruin day':<28} {str(res_a['ruin_day']):>18} {str(res_b['ruin_day']):>18}")
    print(f"{'Winrate sesiones (%)':<28} {res_a['winrate_sessions']:>18.2f} {res_b['winrate_sessions']:>18.2f}")
    print("=" * 70)

    build_excel(res_a, res_b)
    print(f"Excel generado: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
