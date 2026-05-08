"""
Backtest Comparativo: Sistema B (original) vs Sistema B+ (mejorado)

Sistema B  — original del prompt maestro:
  base=$2.00  escudo=$1.00  max_señales=6   revancha=NO

Sistema B+ — con ajustes de recuperación:
  base=$2.00  escudo=$3.50  max_señales=6   revancha=10 si no hay L

El "Escudo Progresivo" ($3.50) reemplaza al escudo de $1.
  Razón: con $3.50 necesitas 9 victorias para recuperar -$30,
  en lugar de 16 con $1.00.

El "Modo Revancha Controlada": si la sesión llega a 6 señales
  sin haber tocado un L (y sin haber alcanzado 2 victorias),
  se extiende hasta 10 señales para dar tiempo a cerrar en Meta.

Genera: runtime/backtest_b_plus.xlsx (5 pestañas + 4 gráficos)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "ejemplo.md"
OUTPUT_PATH = ROOT / "runtime" / "backtest_b_plus.xlsx"

INITIAL_CAPITAL = 300.0
PAYOUT = 0.92
BASE_ENTRY = 2.0

G1_MULT = (1.0 + PAYOUT) / PAYOUT
G2_MULT = G1_MULT * G1_MULT


@dataclass
class Signal:
    ts: datetime
    day: str
    session_idx: int
    result: str


@dataclass
class SessionResult:
    day: str
    session_num: int
    start_balance: float
    end_balance: float
    pnl: float
    operated: int
    wins: int
    losses: int
    close_reason: str   # Meta / Stop / Revancha / Max / Fin ventana
    shield_used: bool


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
        if result:
            out.append(Signal(ts=ts, day=ts.strftime("%d/%m/%Y"), session_idx=ts.hour // 2, result=result))
    out.sort(key=lambda s: s.ts)
    return out


def pnl_for(result: str, entry: float) -> float:
    g1 = round(entry * G1_MULT, 2)
    g2 = round(entry * G2_MULT, 2)
    if result == "WD":
        return round(entry * PAYOUT, 2)
    if result == "G1":
        return round(g1 * PAYOUT - entry, 2)
    if result == "G2":
        return round(g2 * PAYOUT - entry - g1, 2)
    return round(-(entry + g1 + g2), 2)


def run(
    signals: list[Signal],
    shield_entry: float,
    max_normal: int,
    max_revancha: int,
    label: str,
) -> tuple[float, float, float, int, list[SessionResult], list[dict]]:
    """
    Corre una variante del Sistema B sobre todas las señales.

    Retorna: (balance_final, max_dd, first_break_signal_or_0,
              traded_count, sessions, daily_rows)
    """
    balance = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0.0
    traded = 0
    first_break: int = 0
    op_counter = 0
    shield_pending = False

    grouped: dict[tuple[str, int], list[Signal]] = {}
    for s in signals:
        grouped.setdefault((s.day, s.session_idx), []).append(s)

    unique_days = sorted(
        {s.day for s in signals},
        key=lambda d: datetime.strptime(d, "%d/%m/%Y"),
    )

    sessions: list[SessionResult] = []
    daily_rows: list[dict] = []

    for day in unique_days:
        day_start = balance

        for sess_num in range(1, 13):
            bucket = grouped.get((day, sess_num - 1), [])
            if not bucket:
                sessions.append(SessionResult(
                    day=day, session_num=sess_num,
                    start_balance=round(balance, 2), end_balance=round(balance, 2),
                    pnl=0.0, operated=0, wins=0, losses=0,
                    close_reason="Sin señales", shield_used=False,
                ))
                continue

            start_bal = balance
            wins = 0
            losses = 0
            operated = 0
            shield_used = shield_pending
            shield_consumed = False
            close_reason = "Fin ventana"
            in_revancha = False

            for sig in bucket:
                # Límite dinámico: normal=max_normal, revancha=max_revancha
                current_limit = max_revancha if in_revancha else max_normal
                if operated >= current_limit:
                    break
                if wins >= 2:
                    break
                if losses >= 1:
                    break

                # Determinar entrada
                entry = BASE_ENTRY
                if shield_pending and not shield_consumed:
                    entry = shield_entry
                    shield_consumed = True
                    shield_pending = False

                pnl = pnl_for(sig.result, entry)
                balance = round(balance + pnl, 2)
                peak = max(peak, balance)
                dd = round(peak - balance, 2)
                max_dd = max(max_dd, dd)
                operated += 1
                traded += 1
                op_counter += 1

                if first_break == 0 and balance <= 0:
                    first_break = op_counter

                if sig.result == "L":
                    losses += 1
                else:
                    wins += 1

                # Activar modo Revancha si llegamos al límite normal sin L ni Meta
                if (not in_revancha
                        and operated >= max_normal
                        and losses == 0
                        and wins < 2
                        and max_revancha > max_normal):
                    in_revancha = True

            if losses >= 1:
                close_reason = "Stop"
                shield_pending = True
            elif wins >= 2:
                close_reason = "Revancha-Meta" if in_revancha else "Meta"
            elif in_revancha:
                close_reason = "Revancha-Max"
            else:
                close_reason = "Max 6"

            sessions.append(SessionResult(
                day=day, session_num=sess_num,
                start_balance=round(start_bal, 2),
                end_balance=round(balance, 2),
                pnl=round(balance - start_bal, 2),
                operated=operated,
                wins=wins,
                losses=losses,
                close_reason=close_reason,
                shield_used=shield_used,
            ))

        daily_rows.append({
            "Fecha": day,
            "Balance Inicio": round(day_start, 2),
            "Balance Cierre": round(balance, 2),
            "P&L Diario": round(balance - day_start, 2),
        })

    return balance, max_dd, first_break, traded, sessions, daily_rows


def kpis(
    balance: float,
    max_dd: float,
    first_break: int,
    traded: int,
    total: int,
    sessions: list[SessionResult],
) -> dict:
    pnl = round(balance - INITIAL_CAPITAL, 2)
    meta_count = sum(1 for s in sessions if "Meta" in s.close_reason)
    stop_count = sum(1 for s in sessions if s.close_reason == "Stop")
    rev_meta = sum(1 for s in sessions if s.close_reason == "Revancha-Meta")
    rev_max = sum(1 for s in sessions if s.close_reason == "Revancha-Max")
    shield_count = sum(1 for s in sessions if s.shield_used)
    surv_pct = round((first_break / total * 100) if first_break else 100.0, 2)

    return {
        "Balance Final ($)": round(balance, 2),
        "P&L Total ($)": pnl,
        "ROI (%)": round(pnl / INITIAL_CAPITAL * 100, 2),
        "Drawdown Máx ($)": round(max_dd, 2),
        "Primera quiebra (señal #)": first_break if first_break else "No quebró",
        "Supervivencia (%)": surv_pct,
        "Señales totales": total,
        "Señales operadas": traded,
        "Señales saltadas": total - traded,
        "% Operadas": round(traded / total * 100, 2),
        "Sesiones Meta": meta_count,
        "Sesiones Stop": stop_count,
        "Sesiones Revancha→Meta": rev_meta,
        "Sesiones Revancha→Max": rev_max,
        "Escudos activados": shield_count,
    }


def sess_df(sessions: list[SessionResult]) -> pd.DataFrame:
    rows = []
    for s in sessions:
        rows.append({
            "Fecha": s.day,
            "Sesión": s.session_num,
            "Inicio": s.start_balance,
            "Cierre": s.end_balance,
            "P&L": s.pnl,
            "Operadas": s.operated,
            "Victorias": s.wins,
            "Pérdidas": s.losses,
            "Motivo": s.close_reason,
            "Escudo": "Sí" if s.shield_used else "No",
        })
    return pd.DataFrame(rows)


def build_excel(
    kpi_b: dict, kpi_bplus: dict,
    daily_b: list[dict], daily_bplus: list[dict],
    sessions_b: list[SessionResult], sessions_bplus: list[SessionResult],
) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(OUTPUT_PATH, engine="xlsxwriter") as writer:
        wb = writer.book

        # Formatos
        def fmt(**kw):
            return wb.add_format({"border": 1, "align": "center", "valign": "vcenter", **kw})

        f_title = fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", font_size=13)
        f_hdr_b = fmt(bold=True, font_color="#FFFFFF", bg_color="#8B1A1A")     # rojo oscuro → B original
        f_hdr_bp = fmt(bold=True, font_color="#FFFFFF", bg_color="#1E6F4B")    # verde → B+
        f_hdr_neu = fmt(bold=True, font_color="#FFFFFF", bg_color="#2E75B6")   # azul neutro
        f_kpi_lbl = fmt(bold=True, align="left")
        f_money = fmt(num_format="$#,##0.00")
        f_pct = fmt(num_format='0.00"%"')
        f_int = fmt(num_format="0")
        f_cell = fmt()
        f_green_money = fmt(num_format="$#,##0.00", font_color="#1E6F4B", bold=True)
        f_red_money = fmt(num_format="$#,##0.00", font_color="#8B1A1A", bold=True)

        # ── Pestaña 1: Comparativa KPIs ─────────────────────────────────────
        ws1 = wb.add_worksheet("Comparativa KPIs")
        writer.sheets["Comparativa KPIs"] = ws1
        ws1.hide_gridlines(2)
        ws1.set_column("A:A", 35)
        ws1.set_column("B:C", 22)
        ws1.set_column("D:D", 24)

        ws1.merge_range("A1:D1", "Comparativa: Sistema B vs Sistema B+", f_title)
        ws1.merge_range(
            "A2:D2",
            "Base $300 | 2,655 señales | Payout 92% | Meta: 2 y Fuera | Stop: 1 L",
            fmt(bold=True, font_color="#FFFFFF", bg_color="#2E75B6"),
        )

        ws1.write(3, 0, "KPI", f_hdr_neu)
        ws1.write(3, 1, "Sistema B (original)\nescudo=$1  max=6", f_hdr_b)
        ws1.write(3, 2, "Sistema B+ (mejorado)\nescudo=$3.50  revancha=10", f_hdr_bp)
        ws1.write(3, 3, "Diferencia (B+ − B)", f_hdr_neu)
        ws1.set_row(3, 36)

        for i, (k, vb) in enumerate(kpi_b.items(), start=4):
            even = i % 2 == 0
            bg = "F0F4FF" if even else "FFFFFF"
            f_l = fmt(bold=True, align="left", bg_color=bg)
            f_v = fmt(bg_color=bg, num_format="$#,##0.00" if "$" in k else ("0.00" if "%" in k else "0"))
            ws1.write(i, 0, k, f_l)
            vbplus = kpi_bplus.get(k, "—")

            def write_val(row, col, v, kk):
                if isinstance(v, float) and "$" in kk:
                    ws1.write_number(row, col, v, fmt(bg_color=bg, num_format="$#,##0.00"))
                elif isinstance(v, float) and "%" in kk:
                    ws1.write_number(row, col, v, fmt(bg_color=bg, num_format='0.00"%"'))
                elif isinstance(v, float):
                    ws1.write_number(row, col, v, fmt(bg_color=bg))
                else:
                    ws1.write(row, col, v, fmt(bg_color=bg))

            write_val(i, 1, vb, k)
            write_val(i, 2, vbplus, k)

            if isinstance(vb, float) and isinstance(vbplus, float):
                diff = round(vbplus - vb, 2)
                color = "#1E6F4B" if diff >= 0 else "#8B1A1A"
                ws1.write_number(i, 3, diff, fmt(bg_color=bg, font_color=color, bold=True,
                                                   num_format="$#,##0.00" if "$" in k else ("0.00" if "%" in k else "0.00")))
            else:
                ws1.write(i, 3, "—", fmt(bg_color=bg))
            ws1.set_row(i, 18)

        # ── Pestaña 2: Diario ───────────────────────────────────────────────
        df_b = pd.DataFrame(daily_b)
        df_bp = pd.DataFrame(daily_bplus)
        df_merged = pd.merge(df_b, df_bp, on="Fecha", suffixes=(" B", " B+"))
        df_merged.to_excel(writer, sheet_name="Diario", index=False, startrow=1)

        ws2 = writer.sheets["Diario"]
        ws2.hide_gridlines(2)
        ws2.merge_range("A1:G1", "Balance Diario — Sistema B vs B+", f_title)
        ws2.set_column("A:A", 14)
        ws2.set_column("B:G", 16)

        hdrs = ["Fecha", "Bal Inicio B", "Bal Cierre B", "P&L B",
                "Bal Inicio B+", "Bal Cierre B+", "P&L B+"]
        colors = ["#2E75B6", "#8B1A1A", "#8B1A1A", "#8B1A1A",
                  "#1E6F4B", "#1E6F4B", "#1E6F4B"]
        for c, (h, col) in enumerate(zip(hdrs, colors)):
            ws2.write(2, c, h, fmt(bold=True, font_color="#FFFFFF", bg_color=col))

        for r in range(len(df_merged)):
            row = df_merged.iloc[r]
            even = (r + 3) % 2 == 0
            bg = "FFF0F0" if even else "FFFFFF"
            bg_p = "F0FFF4" if even else "FFFFFF"
            ws2.write(r + 3, 0, str(row["Fecha"]), fmt(bg_color=bg))
            ws2.write_number(r + 3, 1, float(row["Balance Inicio B"]), fmt(bg_color=bg, num_format="$#,##0.00"))
            ws2.write_number(r + 3, 2, float(row["Balance Cierre B"]), fmt(bg_color=bg, num_format="$#,##0.00"))
            pnl_b_val = float(row["P&L Diario B"])
            pnl_bp_val = float(row["P&L Diario B+"])
            ws2.write_number(r + 3, 3, pnl_b_val, fmt(
                bg_color=bg, num_format="$#,##0.00",
                font_color="#1E6F4B" if pnl_b_val >= 0 else "#8B1A1A", bold=True))
            ws2.write_number(r + 3, 4, float(row["Balance Inicio B+"]), fmt(bg_color=bg_p, num_format="$#,##0.00"))
            ws2.write_number(r + 3, 5, float(row["Balance Cierre B+"]), fmt(bg_color=bg_p, num_format="$#,##0.00"))
            ws2.write_number(r + 3, 6, pnl_bp_val, fmt(
                bg_color=bg_p, num_format="$#,##0.00",
                font_color="#1E6F4B" if pnl_bp_val >= 0 else "#8B1A1A", bold=True))

        # ── Pestaña 3: Detalle Sistema B ────────────────────────────────────
        df_sb = sess_df(sessions_b)
        df_sb.to_excel(writer, sheet_name="Sesiones B", index=False, startrow=1)
        ws3 = writer.sheets["Sesiones B"]
        ws3.hide_gridlines(2)
        ws3.merge_range("A1:J1", "Detalle de Sesiones — Sistema B (original)", f_hdr_b)
        ws3.set_column("A:A", 13); ws3.set_column("B:B", 8); ws3.set_column("C:F", 14)
        ws3.set_column("G:I", 12); ws3.set_column("J:J", 18)
        for c, h in enumerate(df_sb.columns):
            ws3.write(2, c, h, f_hdr_neu)
        for r in range(len(df_sb)):
            row = df_sb.iloc[r]
            bg = "FFF5F5" if r % 2 == 0 else "FFFFFF"
            for c in range(len(df_sb.columns)):
                v = row.iloc[c]
                f = fmt(bg_color=bg)
                if c in (2, 3, 4):
                    f = fmt(bg_color=bg, num_format="$#,##0.00",
                            font_color=("#1E6F4B" if (c == 4 and isinstance(v, float) and v >= 0)
                                        else ("#8B1A1A" if (c == 4 and isinstance(v, float) and v < 0)
                                              else "#000000")),
                            bold=(c == 4))
                    ws3.write_number(r + 3, c, float(v), f)
                elif isinstance(v, (int, float)):
                    ws3.write_number(r + 3, c, float(v), f)
                else:
                    ws3.write(r + 3, c, str(v), f)

        # ── Pestaña 4: Detalle Sistema B+ ───────────────────────────────────
        df_sbp = sess_df(sessions_bplus)
        df_sbp.to_excel(writer, sheet_name="Sesiones B+", index=False, startrow=1)
        ws4 = writer.sheets["Sesiones B+"]
        ws4.hide_gridlines(2)
        ws4.merge_range("A1:J1", "Detalle de Sesiones — Sistema B+ (mejorado)", f_hdr_bp)
        ws4.set_column("A:A", 13); ws4.set_column("B:B", 8); ws4.set_column("C:F", 14)
        ws4.set_column("G:I", 12); ws4.set_column("J:J", 18)
        for c, h in enumerate(df_sbp.columns):
            ws4.write(2, c, h, f_hdr_neu)
        for r in range(len(df_sbp)):
            row = df_sbp.iloc[r]
            bg = "F0FFF4" if r % 2 == 0 else "FFFFFF"
            for c in range(len(df_sbp.columns)):
                v = row.iloc[c]
                f = fmt(bg_color=bg)
                if c in (2, 3, 4):
                    f = fmt(bg_color=bg, num_format="$#,##0.00",
                            font_color=("#1E6F4B" if (c == 4 and isinstance(v, float) and v >= 0)
                                        else ("#8B1A1A" if (c == 4 and isinstance(v, float) and v < 0)
                                              else "#000000")),
                            bold=(c == 4))
                    ws4.write_number(r + 3, c, float(v), f)
                elif isinstance(v, (int, float)):
                    ws4.write_number(r + 3, c, float(v), f)
                else:
                    ws4.write(r + 3, c, str(v), f)

        # ── Pestaña 5: Gráficos ─────────────────────────────────────────────
        ws5 = wb.add_worksheet("Gráficos")
        writer.sheets["Gráficos"] = ws5
        ws5.hide_gridlines(2)
        ws5.set_column("A:A", 14)
        ws5.set_column("B:J", 16)

        ws5.merge_range("A1:H1", "Visualización Comparativa Sistema B vs B+", f_title)

        # Datos para gráficos
        hdr_g = ["Fecha", "Cierre B", "Cierre B+", "P&L B", "P&L B+",
                 "Gan B", "Perd B", "Gan B+", "Perd B+"]
        for c, h in enumerate(hdr_g):
            ws5.write(2, c, h, f_hdr_neu)

        for i, (rb, rbp) in enumerate(zip(daily_b, daily_bplus)):
            r = i + 3
            pnl_b_v = float(rb["P&L Diario"])
            pnl_bp_v = float(rbp["P&L Diario"])
            ws5.write(r, 0, rb["Fecha"])
            ws5.write_number(r, 1, float(rb["Balance Cierre"]))
            ws5.write_number(r, 2, float(rbp["Balance Cierre"]))
            ws5.write_number(r, 3, pnl_b_v)
            ws5.write_number(r, 4, pnl_bp_v)
            ws5.write_number(r, 5, pnl_b_v if pnl_b_v > 0 else 0.0)
            ws5.write_number(r, 6, pnl_b_v if pnl_b_v < 0 else 0.0)
            ws5.write_number(r, 7, pnl_bp_v if pnl_bp_v > 0 else 0.0)
            ws5.write_number(r, 8, pnl_bp_v if pnl_bp_v < 0 else 0.0)

        n = len(daily_b)
        last = 3 + n - 1

        # Gráfico 1: Equity Curve comparativa (líneas)
        eq_chart = wb.add_chart({"type": "line"})
        eq_chart.add_series({
            "name": "Sistema B (original)",
            "categories": ["Gráficos", 3, 0, last, 0],
            "values": ["Gráficos", 3, 1, last, 1],
            "line": {"color": "#8B1A1A", "width": 2.0},
        })
        eq_chart.add_series({
            "name": "Sistema B+ (mejorado)",
            "categories": ["Gráficos", 3, 0, last, 0],
            "values": ["Gráficos", 3, 2, last, 2],
            "line": {"color": "#1E6F4B", "width": 2.5, "dash_type": "solid"},
        })
        eq_chart.set_title({"name": "Equity Curve: B vs B+"})
        eq_chart.set_y_axis({"name": "Balance (USD)"})
        eq_chart.set_x_axis({"name": "Día"})
        ws5.insert_chart("J2", eq_chart, {"x_scale": 1.8, "y_scale": 1.4})

        # Gráfico 2: P&L diario B (verde/rojo)
        pnl_b_chart = wb.add_chart({"type": "column"})
        pnl_b_chart.add_series({
            "name": "Ganancias B",
            "categories": ["Gráficos", 3, 0, last, 0],
            "values": ["Gráficos", 3, 5, last, 5],
            "fill": {"color": "#1E6F4B"},
        })
        pnl_b_chart.add_series({
            "name": "Pérdidas B",
            "categories": ["Gráficos", 3, 0, last, 0],
            "values": ["Gráficos", 3, 6, last, 6],
            "fill": {"color": "#8B1A1A"},
        })
        pnl_b_chart.set_title({"name": "P&L Diario — Sistema B"})
        pnl_b_chart.set_y_axis({"name": "USD"})
        ws5.insert_chart("J22", pnl_b_chart, {"x_scale": 1.8, "y_scale": 1.4})

        # Gráfico 3: P&L diario B+
        pnl_bp_chart = wb.add_chart({"type": "column"})
        pnl_bp_chart.add_series({
            "name": "Ganancias B+",
            "categories": ["Gráficos", 3, 0, last, 0],
            "values": ["Gráficos", 3, 7, last, 7],
            "fill": {"color": "#1E6F4B"},
        })
        pnl_bp_chart.add_series({
            "name": "Pérdidas B+",
            "categories": ["Gráficos", 3, 0, last, 0],
            "values": ["Gráficos", 3, 8, last, 8],
            "fill": {"color": "#8B1A1A"},
        })
        pnl_bp_chart.set_title({"name": "P&L Diario — Sistema B+"})
        pnl_bp_chart.set_y_axis({"name": "USD"})
        ws5.insert_chart("J42", pnl_bp_chart, {"x_scale": 1.8, "y_scale": 1.4})

        # Gráfico 4: Pastel distribución de cierres (B+)
        meta_bp = sum(1 for s in sessions_bplus if "Meta" in s.close_reason)
        stop_bp = sum(1 for s in sessions_bplus if s.close_reason == "Stop")
        rev_meta = sum(1 for s in sessions_bplus if s.close_reason == "Revancha-Meta")
        rev_max = sum(1 for s in sessions_bplus if s.close_reason == "Revancha-Max")

        pie_row = last + 5
        ws5.write(pie_row, 0, "Tipo Cierre (B+)", f_hdr_bp)
        ws5.write(pie_row, 1, "Cantidad", f_hdr_bp)
        pie_data = [("Meta directa", meta_bp), ("Stop", stop_bp),
                    ("Revancha→Meta", rev_meta), ("Revancha→Max", rev_max)]
        pie_colors = ["#1E6F4B", "#8B1A1A", "#2E75B6", "#E26B0A"]
        for j, (lbl, cnt) in enumerate(pie_data):
            ws5.write(pie_row + 1 + j, 0, lbl, f_cell)
            ws5.write_number(pie_row + 1 + j, 1, cnt, f_cell)

        pie_chart = wb.add_chart({"type": "pie"})
        pie_chart.add_series({
            "name": "Distribución B+",
            "categories": ["Gráficos", pie_row + 1, 0, pie_row + 4, 0],
            "values": ["Gráficos", pie_row + 1, 1, pie_row + 4, 1],
            "data_labels": {"percentage": True, "value": True},
            "points": [{"fill": {"color": c}} for c in pie_colors],
        })
        pie_chart.set_title({"name": "Sesiones B+: Motivo de Cierre"})
        ws5.insert_chart("A4", pie_chart, {"x_scale": 1.3, "y_scale": 1.2})


def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"No se encontró {INPUT_PATH}")
    signals = parse_signals(INPUT_PATH)
    total = len(signals)

    print(f"Señales cargadas: {total}")
    print()

    # Sistema B original
    print("Corriendo Sistema B (escudo=$1.00, max=6)…")
    bal_b, dd_b, brk_b, tr_b, sess_b, daily_b = run(
        signals, shield_entry=1.0, max_normal=6, max_revancha=6, label="B"
    )
    kpi_b = kpis(bal_b, dd_b, brk_b, tr_b, total, sess_b)

    # Sistema B+ mejorado
    print("Corriendo Sistema B+ (escudo=$3.50, revancha=10)…")
    bal_bp, dd_bp, brk_bp, tr_bp, sess_bp, daily_bp = run(
        signals, shield_entry=3.50, max_normal=6, max_revancha=10, label="B+"
    )
    kpi_bp = kpis(bal_bp, dd_bp, brk_bp, tr_bp, total, sess_bp)

    print()
    print("=" * 55)
    print(f"{'KPI':<32} {'B':>10} {'B+':>10}")
    print("=" * 55)
    for k in kpi_b:
        vb = kpi_b[k]
        vbp = kpi_bp[k]
        vb_s = f"${vb:.2f}" if isinstance(vb, float) else str(vb)
        vbp_s = f"${vbp:.2f}" if isinstance(vbp, float) else str(vbp)
        print(f"  {k:<30} {vb_s:>10} {vbp_s:>10}")

    print()
    print("Generando Excel…")
    build_excel(kpi_b, kpi_bp, daily_b, daily_bp, sess_b, sess_bp)
    print(f"Informe guardado en: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
