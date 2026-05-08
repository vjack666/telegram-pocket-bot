from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "ejemplo.md"
OUTPUT_PATH = ROOT / "runtime" / "backtest_maestro_sistema_b.xlsx"

INITIAL_CAPITAL = 300.0
PAYOUT = 0.92
BASE_ENTRY = 2.0
SHIELD_ENTRY = 1.0

G1_MULT = (1.0 + PAYOUT) / PAYOUT
G2_MULT = G1_MULT * G1_MULT


@dataclass
class Signal:
    ts: datetime
    day: str
    session_idx: int  # 0..11
    result: str  # WD | G1 | G2 | L


@dataclass
class SessionResult:
    day: str
    session_num: int
    start_balance: float
    end_balance: float
    pnl: float
    operated_signals: int
    skipped_signals: int
    wins: int
    losses: int
    close_reason: str
    shield_first_trade: bool


def parse_signals(path: Path) -> list[Signal]:
    date_pat = re.compile(r"^\[(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}:\d{2})\]")
    res_pat = re.compile(
        r"(VICTORIA DIRECTA|VICTORIA EN 1.*?MARTINGALA|VICTORIA EN 2.*?MARTINGALA|P[EÉ]RDIDA)",
        re.IGNORECASE,
    )
    label_map = {
        "victoria directa": "WD",
        "victoria en 1": "G1",
        "victoria en 2": "G2",
        "perdida": "L",
        "pérdida": "L",
    }

    out: list[Signal] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        d = date_pat.match(line)
        if not d:
            continue
        r = res_pat.search(line)
        if not r:
            continue
        ts = datetime.strptime(" ".join(d.groups()), "%d/%m/%Y %H:%M:%S")
        raw = r.group(1).lower()
        result = next((v for k, v in label_map.items() if raw.startswith(k)), None)
        if result is None:
            continue
        out.append(
            Signal(
                ts=ts,
                day=ts.strftime("%d/%m/%Y"),
                session_idx=ts.hour // 2,  # 12 sesiones diarias
                result=result,
            )
        )
    out.sort(key=lambda s: s.ts)
    return out


def pnl_from_result(result: str, entry: float) -> float:
    g1 = round(entry * G1_MULT, 2)
    g2 = round(entry * G2_MULT, 2)

    if result == "WD":
        return round(entry * PAYOUT, 2)
    if result == "G1":
        return round(g1 * PAYOUT - entry, 2)
    if result == "G2":
        return round(g2 * PAYOUT - entry - g1, 2)
    return round(-(entry + g1 + g2), 2)


def run_backtest(signals: list[Signal]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    balance = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0.0

    # Agrupar señales por (día, sesión)
    grouped: dict[tuple[str, int], list[Signal]] = {}
    for s in signals:
        grouped.setdefault((s.day, s.session_idx), []).append(s)

    unique_days = sorted({s.day for s in signals}, key=lambda d: datetime.strptime(d, "%d/%m/%Y"))

    sessions: list[SessionResult] = []
    daily_rows: list[dict[str, float | str]] = []

    traded_signals = 0
    skipped_signals = 0
    shield_next_session_first_trade = False

    first_break_signal_idx: int | None = None
    operated_counter = 0

    for day in unique_days:
        day_start = balance
        day_end = balance

        for session_num in range(1, 13):
            key = (day, session_num - 1)
            bucket = grouped.get(key, [])

            start_bal = balance
            wins = 0
            losses = 0
            operated = 0
            skipped = 0
            close_reason = "Sin señales"

            shield_used = shield_next_session_first_trade
            shield_consumed_in_session = False

            if bucket:
                for i, sig in enumerate(bucket):
                    if operated >= 6:
                        skipped += 1
                        continue

                    if wins >= 2:
                        skipped += 1
                        continue

                    if losses >= 1:
                        skipped += 1
                        continue

                    entry = BASE_ENTRY
                    if shield_next_session_first_trade and not shield_consumed_in_session:
                        entry = SHIELD_ENTRY
                        shield_consumed_in_session = True
                        shield_next_session_first_trade = False

                    pnl = pnl_from_result(sig.result, entry)
                    balance = round(balance + pnl, 2)
                    peak = max(peak, balance)
                    dd = round(peak - balance, 2)
                    max_dd = max(max_dd, dd)

                    operated += 1
                    operated_counter += 1
                    traded_signals += 1

                    if first_break_signal_idx is None and balance <= 0:
                        first_break_signal_idx = operated_counter

                    if sig.result == "L":
                        losses += 1
                    else:
                        wins += 1

                if losses >= 1:
                    close_reason = "Stop"
                    shield_next_session_first_trade = True
                elif wins >= 2:
                    close_reason = "Meta"
                elif operated >= 6:
                    close_reason = "Max 6 señales"
                else:
                    close_reason = "Fin ventana"
            else:
                close_reason = "Sin señales"

            skipped_signals += skipped
            end_bal = balance
            sessions.append(
                SessionResult(
                    day=day,
                    session_num=session_num,
                    start_balance=round(start_bal, 2),
                    end_balance=round(end_bal, 2),
                    pnl=round(end_bal - start_bal, 2),
                    operated_signals=operated,
                    skipped_signals=skipped,
                    wins=wins,
                    losses=losses,
                    close_reason=close_reason,
                    shield_first_trade=shield_used,
                )
            )

            day_end = balance

        daily_rows.append(
            {
                "Fecha": day,
                "Balance Inicio": round(day_start, 2),
                "Balance Cierre": round(day_end, 2),
                "P&L Diario": round(day_end - day_start, 2),
            }
        )

    total_signals = len(signals)
    true_skipped = max(0, total_signals - traded_signals)

    pnl_total = round(balance - INITIAL_CAPITAL, 2)
    roi_pct = round((pnl_total / INITIAL_CAPITAL) * 100.0, 2)

    if first_break_signal_idx is None:
        survival_pct = 100.0
    else:
        survival_pct = round((first_break_signal_idx / total_signals) * 100.0, 2)

    meta_count = sum(1 for s in sessions if s.close_reason == "Meta")
    stop_count = sum(1 for s in sessions if s.close_reason == "Stop")

    kpis = {
        "Balance Final": round(balance, 2),
        "P&L Total": pnl_total,
        "ROI %": roi_pct,
        "Drawdown Máximo": round(max_dd, 2),
        "% Supervivencia": survival_pct,
        "Señales Totales": float(total_signals),
        "Señales Operadas": float(traded_signals),
        "Señales Saltadas": float(true_skipped),
        "% Operadas": round((traded_signals / total_signals) * 100.0, 2),
        "% Saltadas": round((true_skipped / total_signals) * 100.0, 2),
        "Sesiones Meta": float(meta_count),
        "Sesiones Stop": float(stop_count),
    }

    df_daily = pd.DataFrame(daily_rows)
    df_sessions = pd.DataFrame([s.__dict__ for s in sessions])
    return df_daily, df_sessions, kpis


def build_excel(df_daily: pd.DataFrame, df_sessions: pd.DataFrame, kpis: dict[str, float]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(OUTPUT_PATH, engine="xlsxwriter") as writer:
        wb = writer.book

        fmt_title = wb.add_format({
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#1F3864",
            "align": "center",
            "valign": "vcenter",
            "font_size": 14,
            "border": 1,
        })
        fmt_hdr = wb.add_format({
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#2E75B6",
            "align": "center",
            "valign": "vcenter",
            "border": 1,
        })
        fmt_cell = wb.add_format({"border": 1, "align": "center", "valign": "vcenter"})
        fmt_money = wb.add_format({"border": 1, "num_format": "$#,##0.00", "align": "center"})
        fmt_pct = wb.add_format({"border": 1, "num_format": "0.00%", "align": "center"})

        # Pestaña 1: Resumen
        ws1 = wb.add_worksheet("Resumen")
        writer.sheets["Resumen"] = ws1
        ws1.hide_gridlines(2)
        ws1.set_column("A:A", 40)
        ws1.set_column("B:B", 24)

        ws1.merge_range("A1:B1", "Backtest Sistema B — Resumen Ejecutivo", fmt_title)

        resumen_rows = [
            ("Balance Final", kpis["Balance Final"], "money"),
            ("P&L Total", kpis["P&L Total"], "money"),
            ("ROI", kpis["ROI %"] / 100.0, "pct"),
            ("Drawdown Máximo", kpis["Drawdown Máximo"], "money"),
            ("% Supervivencia", kpis["% Supervivencia"] / 100.0, "pct"),
            ("Señales Totales", kpis["Señales Totales"], "int"),
            ("Señales Operadas", kpis["Señales Operadas"], "int"),
            ("Señales Saltadas", kpis["Señales Saltadas"], "int"),
            ("% Operadas", kpis["% Operadas"] / 100.0, "pct"),
            ("% Saltadas", kpis["% Saltadas"] / 100.0, "pct"),
        ]

        ws1.write(2, 0, "KPI", fmt_hdr)
        ws1.write(2, 1, "Valor", fmt_hdr)

        for i, (name, val, kind) in enumerate(resumen_rows, start=3):
            ws1.write(i, 0, name, fmt_cell)
            if kind == "money":
                ws1.write_number(i, 1, float(val), fmt_money)
            elif kind == "pct":
                ws1.write_number(i, 1, float(val), fmt_pct)
            elif kind == "int":
                ws1.write_number(i, 1, float(val), fmt_cell)
            else:
                ws1.write(i, 1, val, fmt_cell)

        # Pestaña 2: Diario
        df_daily_out = df_daily.copy()
        df_daily_out.to_excel(writer, sheet_name="Diario", index=False)
        ws2 = writer.sheets["Diario"]
        ws2.hide_gridlines(2)
        ws2.set_column("A:A", 14)
        ws2.set_column("B:D", 16)

        for col_idx, col_name in enumerate(df_daily_out.columns):
            ws2.write(0, col_idx, col_name, fmt_hdr)

        for r in range(1, len(df_daily_out) + 1):
            ws2.write(r, 0, df_daily_out.iloc[r - 1, 0], fmt_cell)
            ws2.write_number(r, 1, float(df_daily_out.iloc[r - 1, 1]), fmt_money)
            ws2.write_number(r, 2, float(df_daily_out.iloc[r - 1, 2]), fmt_money)
            ws2.write_number(r, 3, float(df_daily_out.iloc[r - 1, 3]), fmt_money)

        # Pestaña 3: Detalle de Sesiones
        df_sessions_out = df_sessions.copy()
        df_sessions_out.columns = [
            "Fecha",
            "Sesión",
            "Balance Inicio",
            "Balance Fin",
            "P&L Sesión",
            "Señales Operadas",
            "Señales Saltadas",
            "Victorias",
            "Pérdidas",
            "Motivo Cierre",
            "Escudo Activado",
        ]
        df_sessions_out.to_excel(writer, sheet_name="Detalle de Sesiones", index=False)
        ws3 = writer.sheets["Detalle de Sesiones"]
        ws3.hide_gridlines(2)
        ws3.set_column("A:A", 12)
        ws3.set_column("B:B", 9)
        ws3.set_column("C:E", 14)
        ws3.set_column("F:I", 14)
        ws3.set_column("J:J", 18)
        ws3.set_column("K:K", 14)

        for col_idx, col_name in enumerate(df_sessions_out.columns):
            ws3.write(0, col_idx, col_name, fmt_hdr)

        for r in range(1, len(df_sessions_out) + 1):
            ws3.write(r, 0, str(df_sessions_out.iloc[r - 1, 0]), fmt_cell)
            ws3.write_number(r, 1, float(df_sessions_out.iloc[r - 1, 1]), fmt_cell)
            ws3.write_number(r, 2, float(df_sessions_out.iloc[r - 1, 2]), fmt_money)
            ws3.write_number(r, 3, float(df_sessions_out.iloc[r - 1, 3]), fmt_money)
            ws3.write_number(r, 4, float(df_sessions_out.iloc[r - 1, 4]), fmt_money)
            ws3.write_number(r, 5, float(df_sessions_out.iloc[r - 1, 5]), fmt_cell)
            ws3.write_number(r, 6, float(df_sessions_out.iloc[r - 1, 6]), fmt_cell)
            ws3.write_number(r, 7, float(df_sessions_out.iloc[r - 1, 7]), fmt_cell)
            ws3.write_number(r, 8, float(df_sessions_out.iloc[r - 1, 8]), fmt_cell)
            ws3.write(r, 9, str(df_sessions_out.iloc[r - 1, 9]), fmt_cell)
            ws3.write(r, 10, "Sí" if bool(df_sessions_out.iloc[r - 1, 10]) else "No", fmt_cell)

        # Pestaña 4: Gráficos
        ws4 = wb.add_worksheet("Gráficos")
        writer.sheets["Gráficos"] = ws4
        ws4.hide_gridlines(2)
        ws4.set_column("A:A", 14)
        ws4.set_column("B:H", 14)

        ws4.merge_range("A1:H1", "Visualización del Backtest Sistema B", fmt_title)

        # Datos auxiliares para gráficos
        chart_df = df_daily_out.copy()
        chart_df["Ganancia"] = chart_df["P&L Diario"].apply(lambda x: x if x > 0 else 0.0)
        chart_df["Pérdida"] = chart_df["P&L Diario"].apply(lambda x: x if x < 0 else 0.0)

        start_row = 2
        headers = ["Fecha", "Balance Cierre", "P&L Diario", "Ganancia", "Pérdida"]
        for c, h in enumerate(headers):
            ws4.write(start_row, c, h, fmt_hdr)

        for i, row in chart_df.iterrows():
            rr = start_row + 1 + i
            ws4.write(rr, 0, row["Fecha"], fmt_cell)
            ws4.write_number(rr, 1, float(row["Balance Cierre"]), fmt_money)
            ws4.write_number(rr, 2, float(row["P&L Diario"]), fmt_money)
            ws4.write_number(rr, 3, float(row["Ganancia"]), fmt_money)
            ws4.write_number(rr, 4, float(row["Pérdida"]), fmt_money)

        last_row = start_row + len(chart_df)

        # Gráfico 1: Equity curve
        line_chart = wb.add_chart({"type": "line"})
        line_chart.add_series(
            {
                "name": "Curva de Equidad",
                "categories": ["Gráficos", start_row + 1, 0, last_row, 0],
                "values": ["Gráficos", start_row + 1, 1, last_row, 1],
                "line": {"color": "#2E75B6", "width": 2.25},
            }
        )
        line_chart.set_title({"name": "Curva de Equidad Diaria"})
        line_chart.set_y_axis({"name": "Balance (USD)"})
        line_chart.set_x_axis({"name": "Día"})
        line_chart.set_legend({"none": True})

        # Gráfico 2: P&L diario con color profesional
        col_chart = wb.add_chart({"type": "column"})
        col_chart.add_series(
            {
                "name": "Ganancias",
                "categories": ["Gráficos", start_row + 1, 0, last_row, 0],
                "values": ["Gráficos", start_row + 1, 3, last_row, 3],
                "fill": {"color": "#1E6F4B"},
                "border": {"color": "#1E6F4B"},
            }
        )
        col_chart.add_series(
            {
                "name": "Pérdidas",
                "categories": ["Gráficos", start_row + 1, 0, last_row, 0],
                "values": ["Gráficos", start_row + 1, 4, last_row, 4],
                "fill": {"color": "#8B1A1A"},
                "border": {"color": "#8B1A1A"},
            }
        )
        col_chart.set_title({"name": "P&L Diario"})
        col_chart.set_y_axis({"name": "USD"})
        col_chart.set_x_axis({"name": "Día"})

        # Gráfico 3: Pie de cierres Meta vs Stop
        pie_start = last_row + 3
        ws4.write(pie_start, 0, "Tipo Cierre", fmt_hdr)
        ws4.write(pie_start, 1, "Cantidad", fmt_hdr)
        ws4.write(pie_start + 1, 0, "Meta", fmt_cell)
        ws4.write_number(pie_start + 1, 1, float(kpis["Sesiones Meta"]), fmt_cell)
        ws4.write(pie_start + 2, 0, "Stop", fmt_cell)
        ws4.write_number(pie_start + 2, 1, float(kpis["Sesiones Stop"]), fmt_cell)

        pie_chart = wb.add_chart({"type": "pie"})
        pie_chart.add_series(
            {
                "name": "Distribución de Cierres",
                "categories": ["Gráficos", pie_start + 1, 0, pie_start + 2, 0],
                "values": ["Gráficos", pie_start + 1, 1, pie_start + 2, 1],
                "data_labels": {"percentage": True, "value": True},
                "points": [
                    {"fill": {"color": "#1E6F4B"}},
                    {"fill": {"color": "#8B1A1A"}},
                ],
            }
        )
        pie_chart.set_title({"name": "Sesiones: Meta vs Stop"})

        ws4.insert_chart("G3", line_chart, {"x_scale": 1.4, "y_scale": 1.35})
        ws4.insert_chart("G21", col_chart, {"x_scale": 1.4, "y_scale": 1.35})
        ws4.insert_chart("A21", pie_chart, {"x_scale": 1.2, "y_scale": 1.2})


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"No existe {INPUT_PATH}")

    signals = parse_signals(INPUT_PATH)
    if not signals:
        raise RuntimeError("No se pudieron parsear señales desde ejemplo.md")

    df_daily, df_sessions, kpis = run_backtest(signals)
    build_excel(df_daily, df_sessions, kpis)

    print("Backtest completado")
    print(f"Archivo generado: {OUTPUT_PATH}")
    print(
        "KPIs clave: "
        f"BalanceFinal=${kpis['Balance Final']:.2f} | "
        f"ROI={kpis['ROI %']:.2f}% | "
        f"MaxDD=${kpis['Drawdown Máximo']:.2f} | "
        f"Operadas={int(kpis['Señales Operadas'])}/{int(kpis['Señales Totales'])}"
    )


if __name__ == "__main__":
    main()
