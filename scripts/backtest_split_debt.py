"""
Backtest: Split-Debt Recovery (Simulación de Recuperación Fragmentada)

Estrategia:
  Fase Estándar:
    Entrada base $2.00 | martingala 3 pasos (WD / G1 / G2 / L)

  Fase de Quiebre (L):
    Registra Deuda = $30.00 (fija por diseño de estrategia)
    Activa Fase de Recuperación Progresiva

  Fase de Recuperación Progresiva:
    Deuda dividida en 2 chunks de $15 c/u
    Entrada de recuperación = $17.00 / 0.92 ≈ $18.4783
      → cada WIN (WD/G1/G2) produce exactamente +$17.00 neto
        ($15 amortiza deuda + $2 ganancia base)
    2 wins consecutivos → deuda saldada → vuelve a Fase Estándar

  Seguridad:
    Segunda L con deuda pendiente → Stop Loss Global
    Bot se detiene para proteger el capital restante

Capital inicial: $300 | Payout: 92% | Dataset: ejemplo.md
Output: runtime/split_debt_recovery.xlsx (4 pestañas + 3 gráficos)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "ejemplo.md"
OUTPUT_PATH = ROOT / "runtime" / "split_debt_recovery.xlsx"

# ── Parámetros ────────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 300.0
PAYOUT = 0.92
BASE_ENTRY = 2.0
G1_MULT = (1.0 + PAYOUT) / PAYOUT          # ≈ 2.08696
G2_MULT = G1_MULT * G1_MULT                 # ≈ 4.35526

DEBT_FIXED = 30.0                           # deuda registrada por cada L
N_CHUNKS = 2                                # partes en que se divide la deuda
CHUNK_DEBT = DEBT_FIXED / N_CHUNKS          # $15 por chunk
CHUNK_NET_TARGET = CHUNK_DEBT + BASE_ENTRY  # $17.00 objetivo por trade
RECOVERY_ENTRY = CHUNK_NET_TARGET / PAYOUT  # ≈ $18.4783


# ── Dataclasses ───────────────────────────────────────────────────────────────
@dataclass
class Signal:
    ts: datetime
    day: str
    result: str
    idx: int  # 1-based global index


@dataclass
class ChunkRecord:
    chunk_num: int | str           # 1, 2, o "STOP"
    signal_idx: int
    signal_date: str
    result: str
    entry_used: float
    pnl: float
    balance_after: float
    note: str


@dataclass
class RecoveryEvent:
    event_id: int
    l_signal_idx: int
    l_signal_date: str
    balance_before_l: float
    balance_after_l: float
    actual_l_pnl: float           # pérdida real (≈ -$14.88 con entrada $2)
    registered_debt: float        # $30 por diseño
    chunks: list[ChunkRecord] = field(default_factory=list)
    completed: bool = False       # True si se pagaron los 2 chunks
    global_stop: bool = False     # True si segunda L interrumpió recuperación


# ── Parser ────────────────────────────────────────────────────────────────────
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
    idx = 0
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
            idx += 1
            out.append(Signal(ts=ts, day=ts.strftime("%d/%m/%Y"), result=result, idx=idx))
    out.sort(key=lambda s: s.ts)
    return out


# ── P&L de una señal martingala ───────────────────────────────────────────────
def pnl_for(result: str, entry: float) -> float:
    """Ganancia/pérdida neta de una señal completa (3 pasos max)."""
    g1 = entry * G1_MULT
    g2 = g1 * G1_MULT
    if result == "WD":
        return round(entry * PAYOUT, 4)
    if result == "G1":
        return round(g1 * PAYOUT - entry, 4)
    if result == "G2":
        return round(g2 * PAYOUT - entry - g1, 4)
    # L: pérdida total de los 3 pasos
    return round(-(entry + g1 + g2), 4)


# ── Motor del backtest ────────────────────────────────────────────────────────
def run_backtest(signals: list[Signal]) -> dict:
    balance = INITIAL_CAPITAL
    peak = INITIAL_CAPITAL
    max_dd = 0.0

    phase: Literal["standard", "recovery", "stopped"] = "standard"
    current_event: RecoveryEvent | None = None
    chunks_remaining = 0
    event_counter = 0

    equity: list[dict] = []
    recovery_events: list[RecoveryEvent] = []
    daily: dict[str, list[float]] = {}        # day → list of balances

    total_l = 0
    total_completed = 0
    total_stops = 0
    last_idx = 0
    stop_signal_idx = 0
    stop_balance = 0.0

    for sig in signals:
        if phase == "stopped":
            break

        last_idx = sig.idx

        if phase == "standard":
            entry = BASE_ENTRY
            pnl = pnl_for(sig.result, entry)
            bal_before = balance
            balance = round(balance + pnl, 4)
            peak = max(peak, balance)
            max_dd = max(max_dd, round(peak - balance, 4))

            equity.append({
                "idx": sig.idx,
                "date": sig.day,
                "result": sig.result,
                "phase": "Estándar",
                "entry": entry,
                "pnl": pnl,
                "balance": balance,
                "balance_std": balance,      # para la gráfica
                "balance_rec": None,
            })
            daily.setdefault(sig.day, []).append(balance)

            if sig.result == "L":
                total_l += 1
                event_counter += 1
                current_event = RecoveryEvent(
                    event_id=event_counter,
                    l_signal_idx=sig.idx,
                    l_signal_date=sig.day,
                    balance_before_l=round(bal_before, 4),
                    balance_after_l=round(balance, 4),
                    actual_l_pnl=pnl,
                    registered_debt=DEBT_FIXED,
                )
                recovery_events.append(current_event)
                chunks_remaining = N_CHUNKS
                phase = "recovery"

        elif phase == "recovery":
            entry = RECOVERY_ENTRY
            pnl = pnl_for(sig.result, entry)
            balance = round(balance + pnl, 4)
            peak = max(peak, balance)
            max_dd = max(max_dd, round(peak - balance, 4))

            equity.append({
                "idx": sig.idx,
                "date": sig.day,
                "result": sig.result,
                "phase": "Recuperación",
                "entry": round(entry, 4),
                "pnl": pnl,
                "balance": balance,
                "balance_std": None,
                "balance_rec": balance,      # para la gráfica
            })
            daily.setdefault(sig.day, []).append(balance)

            if sig.result == "L":
                # Segunda L → Global Stop
                total_stops += 1
                stop_signal_idx = sig.idx
                stop_balance = balance
                current_event.global_stop = True
                current_event.chunks.append(ChunkRecord(
                    chunk_num="STOP",
                    signal_idx=sig.idx,
                    signal_date=sig.day,
                    result=sig.result,
                    entry_used=round(entry, 4),
                    pnl=pnl,
                    balance_after=balance,
                    note="Segunda L → STOP LOSS GLOBAL activado",
                ))
                phase = "stopped"
            else:
                # WIN → amortizar un chunk
                chunk_num = N_CHUNKS - chunks_remaining + 1
                chunks_remaining -= 1
                paid_debt = CHUNK_DEBT
                current_event.chunks.append(ChunkRecord(
                    chunk_num=chunk_num,
                    signal_idx=sig.idx,
                    signal_date=sig.day,
                    result=sig.result,
                    entry_used=round(entry, 4),
                    pnl=pnl,
                    balance_after=balance,
                    note=f"Chunk {chunk_num}/2 — amortiza ${paid_debt:.2f} deuda + ${BASE_ENTRY:.2f} base",
                ))
                if chunks_remaining == 0:
                    current_event.completed = True
                    total_completed += 1
                    phase = "standard"
                    current_event = None

    # Construir tabla diaria
    sorted_days = sorted(daily.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
    prev_bal = INITIAL_CAPITAL
    daily_rows = []
    for day in sorted_days:
        close_bal = daily[day][-1]
        pnl_day = round(close_bal - prev_bal, 4)
        # contar operaciones del día
        day_eq = [e for e in equity if e["date"] == day]
        n_std = sum(1 for e in day_eq if e["phase"] == "Estándar")
        n_rec = sum(1 for e in day_eq if e["phase"] == "Recuperación")
        daily_rows.append({
            "Fecha": day,
            "Bal. Apertura": round(prev_bal, 2),
            "Bal. Cierre": round(close_bal, 2),
            "P&L Diario": round(pnl_day, 2),
            "Op. Estándar": n_std,
            "Op. Recuperación": n_rec,
        })
        prev_bal = close_bal

    # KPIs
    operated = len(equity)
    phases_count = {"Estándar": 0, "Recuperación": 0}
    for e in equity:
        phases_count[e["phase"]] += 1

    balance_final = round(balance, 2)
    pnl_total = round(balance_final - INITIAL_CAPITAL, 2)
    roi = round(pnl_total / INITIAL_CAPITAL * 100, 2)

    if phase == "stopped":
        last_processed = stop_signal_idx
        status = "DETENIDO — Stop Loss Global"
    else:
        last_processed = last_idx
        status = "Completado"

    kpis = {
        "Capital Inicial": INITIAL_CAPITAL,
        "Balance Final": balance_final,
        "P&L Total": pnl_total,
        "ROI (%)": roi,
        "Drawdown Máximo": round(max_dd, 2),
        "Señales totales": len(signals),
        "Señales procesadas": operated,
        "Señales omitidas (post-stop)": len(signals) - operated,
        "Op. en Fase Estándar": phases_count["Estándar"],
        "Op. en Fase Recuperación": phases_count["Recuperación"],
        "Total L (quiebres)": total_l,
        "Recuperaciones completadas": total_completed,
        "Recuperaciones incompletas": total_l - total_completed - total_stops,
        "Stop Loss Globales": total_stops,
        "Estado final": status,
        "Última señal procesada (#)": last_processed,
        "Entrada estándar": f"${BASE_ENTRY:.2f}",
        "Entrada recuperación": f"${RECOVERY_ENTRY:.4f} (~$18.48)",
        "Deuda registrada por L": f"${DEBT_FIXED:.2f}",
        "Chunks de deuda (2 × $15)": f"${CHUNK_DEBT:.2f} c/u",
    }

    return {
        "kpis": kpis,
        "equity": equity,
        "daily_rows": daily_rows,
        "recovery_events": recovery_events,
        "balance_final": balance_final,
        "max_dd": max_dd,
    }


# ── Generador de Excel ────────────────────────────────────────────────────────
def build_excel(result: dict) -> None:
    kpis = result["kpis"]
    equity = result["equity"]
    daily_rows = result["daily_rows"]
    recovery_events: list[RecoveryEvent] = result["recovery_events"]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(OUTPUT_PATH, engine="xlsxwriter") as writer:
        wb = writer.book

        # ── Formatos ─────────────────────────────────────────────────────────
        def fmt(**kw):
            return wb.add_format({"border": 1, "align": "center", "valign": "vcenter", **kw})

        f_title = fmt(bold=True, font_color="#FFFFFF", bg_color="#1F3864", font_size=13)
        f_hdr   = fmt(bold=True, font_color="#FFFFFF", bg_color="#2E75B6")
        f_hdr_r = fmt(bold=True, font_color="#FFFFFF", bg_color="#843C0C")  # naranja deuda
        f_hdr_g = fmt(bold=True, font_color="#FFFFFF", bg_color="#1E6F4B")  # verde ok
        f_hdr_s = fmt(bold=True, font_color="#FFFFFF", bg_color="#843c0c")  # stop rojo
        f_lbl   = fmt(bold=True, align="left")
        f_money = fmt(num_format="$#,##0.00")
        f_pct   = fmt(num_format='0.00"%"')
        f_int   = fmt(num_format="0")
        f_cell  = fmt()
        f_green = fmt(font_color="#1E6F4B", bold=True, num_format="$#,##0.00")
        f_red   = fmt(font_color="#8B1A1A", bold=True, num_format="$#,##0.00")

        # ── Pestaña 1: Resumen ────────────────────────────────────────────────
        ws1 = wb.add_worksheet("Resumen")
        writer.sheets["Resumen"] = ws1
        ws1.hide_gridlines(2)
        ws1.set_column("A:A", 34)
        ws1.set_column("B:B", 30)

        ws1.merge_range("A1:B1", "Split-Debt Recovery — Resumen de Backtest", f_title)
        ws1.merge_range("A2:B2",
            f"Capital $300 | Payout 92% | Base $2 | Deuda $30 (2 × $15) | Recuperación ~$18.48/trade",
            fmt(bold=True, font_color="#FFFFFF", bg_color="#2E75B6"))
        ws1.set_row(2, 18)

        ws1.write(3, 0, "KPI", f_hdr)
        ws1.write(3, 1, "Valor", f_hdr)

        money_keys = {"Capital Inicial", "Balance Final", "P&L Total",
                      "Drawdown Máximo"}
        pct_keys = {"ROI (%)"}
        int_keys = {"Señales totales", "Señales procesadas", "Señales omitidas (post-stop)",
                    "Op. en Fase Estándar", "Op. en Fase Recuperación",
                    "Total L (quiebres)", "Recuperaciones completadas",
                    "Recuperaciones incompletas", "Stop Loss Globales",
                    "Última señal procesada (#)"}

        SECTION_BREAKS = {
            "Señales totales": "Actividad Operativa",
            "Total L (quiebres)": "Split-Debt Recovery",
            "Estado final": "Configuración",
        }

        row = 4
        for k, v in kpis.items():
            if k in SECTION_BREAKS:
                ws1.merge_range(row, 0, row, 1, SECTION_BREAKS[k],
                                fmt(bold=True, font_color="#FFFFFF", bg_color="#595959",
                                    align="center"))
                ws1.set_row(row, 15)
                row += 1

            even = row % 2 == 0
            bg = "EAF4FF" if even else "FFFFFF"
            f_l = fmt(bold=True, align="left", bg_color=bg)

            ws1.write(row, 0, k, f_l)
            if k in money_keys and isinstance(v, float):
                color = "#1E6F4B" if v >= 0 else "#8B1A1A"
                ws1.write_number(row, 1,
                    v, fmt(bg_color=bg, num_format="$#,##0.00",
                            font_color=color, bold=True))
            elif k in pct_keys and isinstance(v, float):
                color = "#1E6F4B" if v >= 0 else "#8B1A1A"
                ws1.write_number(row, 1,
                    v, fmt(bg_color=bg, num_format='0.00"%"',
                            font_color=color, bold=True))
            elif k in int_keys and isinstance(v, int):
                ws1.write_number(row, 1, v, fmt(bg_color=bg))
            else:
                ws1.write(row, 1, str(v), fmt(bg_color=bg))
            ws1.set_row(row, 18)
            row += 1

        # ── Pestaña 2: Diario ─────────────────────────────────────────────────
        df_daily = pd.DataFrame(daily_rows)
        df_daily.to_excel(writer, sheet_name="Diario", index=False, startrow=2)

        ws2 = writer.sheets["Diario"]
        ws2.hide_gridlines(2)
        ws2.merge_range("A1:F1", "Balance Diario — Split-Debt Recovery", f_title)
        ws2.set_column("A:A", 13)
        ws2.set_column("B:C", 16)
        ws2.set_column("D:D", 14)
        ws2.set_column("E:F", 18)

        daily_hdrs = ["Fecha", "Bal. Apertura", "Bal. Cierre",
                      "P&L Diario", "Op. Estándar", "Op. Recuperación"]
        daily_colors = ["#2E75B6"] * 3 + ["#1E6F4B"] + ["#595959"] * 2
        for c, (h, col) in enumerate(zip(daily_hdrs, daily_colors)):
            ws2.write(2, c, h, fmt(bold=True, font_color="#FFFFFF", bg_color=col))

        for r in range(len(df_daily)):
            row_d = df_daily.iloc[r]
            even = (r + 3) % 2 == 0
            bg = "F0F8FF" if even else "FFFFFF"
            ws2.write(r + 3, 0, str(row_d["Fecha"]), fmt(bg_color=bg))
            ws2.write_number(r + 3, 1, float(row_d["Bal. Apertura"]),
                             fmt(bg_color=bg, num_format="$#,##0.00"))
            ws2.write_number(r + 3, 2, float(row_d["Bal. Cierre"]),
                             fmt(bg_color=bg, num_format="$#,##0.00"))
            pnl_v = float(row_d["P&L Diario"])
            ws2.write_number(r + 3, 3, pnl_v,
                             fmt(bg_color=bg, num_format="$#,##0.00",
                                 font_color="#1E6F4B" if pnl_v >= 0 else "#8B1A1A",
                                 bold=True))
            ws2.write_number(r + 3, 4, int(row_d["Op. Estándar"]), fmt(bg_color=bg))
            ws2.write_number(r + 3, 5, int(row_d["Op. Recuperación"]), fmt(bg_color=bg))

        # ── Pestaña 3: Detalle de Recuperaciones ──────────────────────────────
        ws3 = wb.add_worksheet("Detalle de Recuperaciones")
        writer.sheets["Detalle de Recuperaciones"] = ws3
        ws3.hide_gridlines(2)
        ws3.set_column("A:B", 9)
        ws3.set_column("C:D", 13)
        ws3.set_column("E:E", 10)
        ws3.set_column("F:G", 14)
        ws3.set_column("H:I", 14)
        ws3.set_column("J:J", 12)
        ws3.set_column("K:K", 42)

        ws3.merge_range("A1:K1", "Detalle de Recuperaciones — Cómo se pagó cada L", f_title)
        hdrs3 = ["Evento #", "Tipo", "Señal #", "Fecha",
                 "Resultado", "Entrada", "P&L", "Bal. Antes",
                 "Bal. Después", "Chunk", "Nota"]
        for c, h in enumerate(hdrs3):
            ws3.write(2, c, h, f_hdr)
        ws3.set_row(2, 20)

        row3 = 3
        for ev in recovery_events:
            # ── Fila: señal L que originó la deuda ──────────────────────────
            ws3.merge_range(row3, 0, row3, 10,
                f"EVENTO #{ev.event_id}  —  L en señal #{ev.l_signal_idx} ({ev.l_signal_date})"
                f"  |  Pérdida real: ${ev.actual_l_pnl:.2f}"
                f"  |  Deuda registrada: ${ev.registered_debt:.2f}",
                fmt(bold=True, font_color="#FFFFFF", bg_color="#843C0C",
                    align="left", font_size=10))
            ws3.set_row(row3, 18)
            row3 += 1

            # datos de la L
            ws3.write_number(row3, 0, ev.event_id, f_hdr_r)
            ws3.write(row3, 1, "L (quiebre)", f_hdr_r)
            ws3.write_number(row3, 2, ev.l_signal_idx, fmt(bold=True, bg_color="#FFCCAA"))
            ws3.write(row3, 3, ev.l_signal_date, fmt(bg_color="#FFCCAA"))
            ws3.write(row3, 4, "L", fmt(bg_color="#FFCCAA", font_color="#8B1A1A", bold=True))
            ws3.write_number(row3, 5, BASE_ENTRY,
                             fmt(bg_color="#FFCCAA", num_format="$#,##0.00"))
            ws3.write_number(row3, 6, ev.actual_l_pnl,
                             fmt(bg_color="#FFCCAA", num_format="$#,##0.00",
                                 font_color="#8B1A1A", bold=True))
            ws3.write_number(row3, 7, ev.balance_before_l,
                             fmt(bg_color="#FFCCAA", num_format="$#,##0.00"))
            ws3.write_number(row3, 8, ev.balance_after_l,
                             fmt(bg_color="#FFCCAA", num_format="$#,##0.00"))
            ws3.write(row3, 9, "—", fmt(bg_color="#FFCCAA"))
            ws3.write(row3, 10,
                      f"Deuda: ${ev.registered_debt:.2f} dividida en {N_CHUNKS} chunks de ${CHUNK_DEBT:.2f} c/u. "
                      f"Entrada recuperación: ${RECOVERY_ENTRY:.4f}",
                      fmt(bg_color="#FFCCAA", align="left"))
            row3 += 1

            # ── Filas de chunks ──────────────────────────────────────────────
            for ch in ev.chunks:
                is_stop = (str(ch.chunk_num) == "STOP")
                bg = "#FFE0E0" if is_stop else ("#E8F5E9" if ch.chunk_num == 2 else "#F0FFF4")
                f_row = fmt(bg_color=bg)

                ws3.write_number(row3, 0, ev.event_id, f_row)
                ws3.write(row3, 1,
                          "STOP GLOBAL" if is_stop else f"Chunk {ch.chunk_num}/{N_CHUNKS}",
                          fmt(bg_color=bg,
                              font_color="#8B1A1A" if is_stop else "#1E6F4B",
                              bold=True))
                ws3.write_number(row3, 2, ch.signal_idx, f_row)
                ws3.write(row3, 3, ch.signal_date, f_row)
                ws3.write(row3, 4, ch.result,
                          fmt(bg_color=bg,
                              font_color="#8B1A1A" if ch.result == "L" else "#1E6F4B",
                              bold=True))
                ws3.write_number(row3, 5, ch.entry_used,
                                 fmt(bg_color=bg, num_format="$#,##0.00"))
                ws3.write_number(row3, 6, ch.pnl,
                                 fmt(bg_color=bg, num_format="$#,##0.00",
                                     font_color="#1E6F4B" if ch.pnl > 0 else "#8B1A1A",
                                     bold=True))
                ws3.write(row3, 7, "—", f_row)
                ws3.write_number(row3, 8, ch.balance_after,
                                 fmt(bg_color=bg, num_format="$#,##0.00"))
                ws3.write(row3, 9,
                          "STOP" if is_stop else str(ch.chunk_num),
                          fmt(bg_color=bg, bold=True,
                              font_color="#8B1A1A" if is_stop else "#1E6F4B"))
                ws3.write(row3, 10, ch.note, fmt(bg_color=bg, align="left"))
                row3 += 1

            # ── Fila de resumen del evento ───────────────────────────────────
            if ev.completed:
                status_txt = "RECUPERACION COMPLETA — $30 saldados en 2 wins"
                status_bg = "#C6EFCE"
                status_fc = "#1E6F4B"
            elif ev.global_stop:
                status_txt = "STOP LOSS GLOBAL — segunda L durante deuda pendiente"
                status_bg = "#FFC7CE"
                status_fc = "#8B1A1A"
            else:
                status_txt = "EN PROGRESO o INCOMPLETA al final del dataset"
                status_bg = "#FFEB9C"
                status_fc = "#9C5700"
            ws3.merge_range(row3, 0, row3, 10, status_txt,
                            fmt(bold=True, font_color=status_fc, bg_color=status_bg,
                                align="left"))
            ws3.set_row(row3, 15)
            row3 += 1

            # Separador
            ws3.merge_range(row3, 0, row3, 10, "", fmt(bg_color="#CCCCCC"))
            ws3.set_row(row3, 4)
            row3 += 1

        # ── Pestaña 4: Gráfica de Equidad ─────────────────────────────────────
        ws4 = wb.add_worksheet("Gráfica de Equidad")
        writer.sheets["Gráfica de Equidad"] = ws4
        ws4.hide_gridlines(2)
        ws4.set_column("A:A", 10)
        ws4.set_column("B:F", 15)

        ws4.merge_range("A1:F1", "Curva de Equidad — Split-Debt Recovery", f_title)

        # Headers de datos
        hdrs4 = ["Señal #", "Fecha", "Balance", "Estándar", "Recuperación", "Valle (L)"]
        for c, h in enumerate(hdrs4):
            ws4.write(2, c, h, f_hdr)
        ws4.set_row(2, 18)

        # Escribir datos de la curva
        for i, eq in enumerate(equity):
            r = i + 3
            ws4.write_number(r, 0, eq["idx"], f_cell)
            ws4.write(r, 1, eq["date"], f_cell)
            ws4.write_number(r, 2, eq["balance"], f_cell)
            # Serie Estándar (solo cuando fase == Estándar)
            if eq["phase"] == "Estándar":
                ws4.write_number(r, 3, eq["balance"])
                ws4.write(r, 4, None)
            else:
                ws4.write(r, 3, None)
                ws4.write_number(r, 4, eq["balance"])
            # Marcar valles: punto inmediatamente después de L
            ws4.write(r, 5, None)

        # Marcar los valles (balances mínimos después de cada L)
        l_indices = [e["idx"] for e in equity if e["result"] == "L"]
        for l_idx in l_indices:
            row_pos = next((i for i, e in enumerate(equity) if e["idx"] == l_idx), None)
            if row_pos is not None:
                ws4.write_number(row_pos + 3, 5, equity[row_pos]["balance"])

        n_rows = len(equity)
        last_r = 3 + n_rows - 1

        # ── Gráfico principal: equity curve con zonas de recuperación ────────
        eq_chart = wb.add_chart({"type": "line"})

        eq_chart.add_series({
            "name": "Fase Estándar",
            "categories": ["Gráfica de Equidad", 3, 0, last_r, 0],
            "values": ["Gráfica de Equidad", 3, 3, last_r, 3],
            "line": {"color": "#2E75B6", "width": 2.0},
            "marker": {"type": "none"},
        })
        eq_chart.add_series({
            "name": "Fase Recuperación",
            "categories": ["Gráfica de Equidad", 3, 0, last_r, 0],
            "values": ["Gráfica de Equidad", 3, 4, last_r, 4],
            "line": {"color": "#E26B0A", "width": 2.5, "dash_type": "square_dot"},
            "marker": {"type": "none"},
        })
        eq_chart.add_series({
            "name": "Valles (L)",
            "categories": ["Gráfica de Equidad", 3, 0, last_r, 0],
            "values": ["Gráfica de Equidad", 3, 5, last_r, 5],
            "line": {"none": True},
            "marker": {
                "type": "diamond",
                "size": 8,
                "fill": {"color": "#FF0000"},
                "border": {"color": "#8B1A1A"},
            },
        })

        eq_chart.set_title({"name": "Curva de Equidad: Valles y Recuperaciones"})
        eq_chart.set_y_axis({
            "name": "Balance (USD)",
            "major_gridlines": {"visible": True, "line": {"color": "#DDDDDD"}},
        })
        eq_chart.set_x_axis({
            "name": "Señal #",
            "major_unit": max(1, n_rows // 20),
        })
        eq_chart.set_legend({"position": "bottom"})
        ws4.insert_chart("H2", eq_chart, {"x_scale": 2.2, "y_scale": 1.6})

        # ── Gráfico 2: P&L diario barras ──────────────────────────────────────
        # Datos adicionales en columna G/H para P&L diario
        ws4.write(2, 6, "Fecha (diario)", f_hdr)
        ws4.write(2, 7, "Ganancia día", f_hdr)
        ws4.write(2, 8, "Pérdida día", f_hdr)
        for i, dr in enumerate(daily_rows):
            r = i + 3
            pnl_v = float(dr["P&L Diario"])
            ws4.write(r, 6, dr["Fecha"])
            ws4.write_number(r, 7, pnl_v if pnl_v > 0 else 0.0)
            ws4.write_number(r, 8, pnl_v if pnl_v < 0 else 0.0)

        n_days = len(daily_rows)
        last_day = 3 + n_days - 1

        pnl_chart = wb.add_chart({"type": "column"})
        pnl_chart.add_series({
            "name": "Ganancia diaria",
            "categories": ["Gráfica de Equidad", 3, 6, last_day, 6],
            "values": ["Gráfica de Equidad", 3, 7, last_day, 7],
            "fill": {"color": "#1E6F4B"},
        })
        pnl_chart.add_series({
            "name": "Pérdida diaria",
            "categories": ["Gráfica de Equidad", 3, 6, last_day, 6],
            "values": ["Gráfica de Equidad", 3, 8, last_day, 8],
            "fill": {"color": "#8B1A1A"},
        })
        pnl_chart.set_title({"name": "P&L Diario (ganancias vs pérdidas)"})
        pnl_chart.set_y_axis({"name": "USD"})
        ws4.insert_chart("H30", pnl_chart, {"x_scale": 2.2, "y_scale": 1.4})


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"No se encontró {INPUT_PATH}")

    signals = parse_signals(INPUT_PATH)
    print(f"Señales cargadas: {len(signals)}")
    print(f"Entrada estándar: ${BASE_ENTRY:.2f}  |  "
          f"Entrada recuperación: ${RECOVERY_ENTRY:.4f}")
    print(f"Deuda fija por L: ${DEBT_FIXED:.2f}  →  "
          f"{N_CHUNKS} chunks × ${CHUNK_DEBT:.2f}")
    print()

    result = run_backtest(signals)

    kpis = result["kpis"]
    print("=" * 60)
    print(f"{'KPI':<38} {'Valor':>18}")
    print("=" * 60)
    highlight = {"Balance Final", "P&L Total", "ROI (%)", "Drawdown Máximo",
                 "Total L (quiebres)", "Recuperaciones completadas",
                 "Stop Loss Globales", "Estado final"}
    for k, v in kpis.items():
        if k in highlight:
            val_s = f"${v:.2f}" if isinstance(v, float) else str(v)
            print(f"  {k:<36} {val_s:>18}")
    print("=" * 60)
    print()
    print("Generando Excel…")
    build_excel(result)
    print(f"Guardado en: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
