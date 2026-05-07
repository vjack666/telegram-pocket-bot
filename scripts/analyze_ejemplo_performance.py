from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import AppSettings
from src.core.pipeline import MasanielloSessionState
from src.signals.parser import SignalParser

try:
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover
    plt = None
    mdates = None


HEADER_RE = re.compile(r"^\[(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\]\s+([^:]+):\s*(.*)$", re.DOTALL)
TS_FMT = "%d/%m/%Y %H:%M:%S"


@dataclass
class ParsedMessage:
    ts_local: datetime
    channel: str
    text: str


@dataclass
class SignalEvent:
    ts_local: datetime
    channel: str
    asset: str
    side: str
    expiry_minutes: int


@dataclass
class ResolvedTrade:
    signal: SignalEvent
    result_kind: str  # WD | G1 | G2 | L


@dataclass
class TradePoint:
    ts_local: datetime
    result_kind: str
    pnl: float
    equity_after: float
    drawdown_abs: float


def parse_ejemplo(path: Path) -> list[ParsedMessage]:
    raw = path.read_text(encoding="utf-8")
    blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]

    out: list[ParsedMessage] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        m = HEADER_RE.match(block)
        if not m:
            i += 1
            continue

        ts_txt, channel, first_text = m.group(1), m.group(2).strip(), m.group(3).strip()
        ts_local = datetime.strptime(ts_txt, TS_FMT)

        # Acumula texto hasta el próximo header
        text_parts = [first_text]
        j = i + 1
        while j < len(blocks) and not HEADER_RE.match(blocks[j]):
            text_parts.append(blocks[j])
            j += 1

        full_text = "\n\n".join(p for p in text_parts if p)
        out.append(ParsedMessage(ts_local=ts_local, channel=channel, text=full_text))
        i = j

    return out


def classify_result(text: str) -> str | None:
    t = text.upper().replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    if "VICTORIA DIRECTA" in t:
        return "WD"
    if "VICTORIA EN 1" in t and "MARTINGALA" in t:
        return "G1"
    if "VICTORIA EN 2" in t and "MARTINGALA" in t:
        return "G2"
    if "PERDIDA" in t or "PÉRDIDA" in text.upper() or "❌" in text:
        # Evita capturar reportes resumen
        if "INFORME DE OPERACIONES" in t:
            return None
        if "SEÑAL" in t and "PERDIDA" not in t:
            return None
        return "L"
    return None


def _save_charts(output_dir: Path, points: list[TradePoint], initial_equity: float) -> list[Path]:
    if plt is None or mdates is None:
        return []
    if not points:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    times = [p.ts_local for p in points]
    times_num = mdates.date2num(times)
    pnl = [p.pnl for p in points]
    equity = [p.equity_after for p in points]
    dd = [p.drawdown_abs for p in points]

    wins_t = [p.ts_local for p in points if p.result_kind in {"WD", "G1", "G2"}]
    wins_t_num = mdates.date2num(wins_t) if wins_t else []
    wins_y = [p.pnl for p in points if p.result_kind in {"WD", "G1", "G2"}]
    loss_t = [p.ts_local for p in points if p.result_kind == "L"]
    loss_t_num = mdates.date2num(loss_t) if loss_t else []
    loss_y = [p.pnl for p in points if p.result_kind == "L"]

    generated: list[Path] = []

    # 1) Curva de equity + drawdown
    fig1, (ax1, ax1b) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    ax1.plot(times_num, equity, color="#1f77b4", linewidth=1.4, label="Equity")
    ax1.axhline(initial_equity, color="#999999", linestyle="--", linewidth=1, label="Equity inicial")
    ax1.set_title("Equity por operación (desde primer mensaje hasta último)")
    ax1.set_ylabel("Equity")
    ax1.grid(alpha=0.25)
    ax1.legend(loc="best")

    ax1b.fill_between(times_num, dd, color="#d62728", alpha=0.25, label="Drawdown abs")
    ax1b.plot(times_num, dd, color="#d62728", linewidth=1.0)
    ax1b.set_ylabel("Drawdown")
    ax1b.set_xlabel("Fecha/Hora")
    ax1b.grid(alpha=0.25)
    ax1b.legend(loc="best")
    ax1b.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax1b.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    fig1.autofmt_xdate()
    p1 = output_dir / "equity_drawdown_timeline.png"
    fig1.tight_layout()
    fig1.savefig(p1, dpi=160)
    plt.close(fig1)
    generated.append(p1)

    # 2) PnL por operación (ganadas vs perdidas) en el eje temporal
    fig2, ax2 = plt.subplots(figsize=(14, 6))
    ax2.axhline(0, color="#222222", linewidth=1)
    ax2.vlines(times_num, 0, pnl, color="#cccccc", linewidth=0.5, alpha=0.5)
    ax2.scatter(wins_t_num, wins_y, s=12, color="#2ca02c", label="Operación ganada", alpha=0.8)
    ax2.scatter(loss_t_num, loss_y, s=12, color="#d62728", label="Operación perdida", alpha=0.8)
    ax2.set_title("PnL por operación en línea temporal")
    ax2.set_ylabel("PnL por operación")
    ax2.set_xlabel("Fecha/Hora")
    ax2.grid(alpha=0.25)
    ax2.legend(loc="best")
    ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    fig2.autofmt_xdate()
    p2 = output_dir / "pnl_per_operation_timeline.png"
    fig2.tight_layout()
    fig2.savefig(p2, dpi=160)
    plt.close(fig2)
    generated.append(p2)

    # 3) Acumulado de operaciones ganadas/perdidas en el tiempo
    win_cum: list[int] = []
    loss_cum: list[int] = []
    w = 0
    l = 0
    for p in points:
        if p.result_kind in {"WD", "G1", "G2"}:
            w += 1
        else:
            l += 1
        win_cum.append(w)
        loss_cum.append(l)

    fig3, ax3 = plt.subplots(figsize=(14, 6))
    ax3.plot(times_num, win_cum, color="#2ca02c", linewidth=1.5, label="Ganadas acumuladas")
    ax3.plot(times_num, loss_cum, color="#d62728", linewidth=1.5, label="Perdidas acumuladas")
    ax3.set_title("Acumulado de operaciones ganadas vs perdidas")
    ax3.set_ylabel("Cantidad acumulada")
    ax3.set_xlabel("Fecha/Hora")
    ax3.grid(alpha=0.25)
    ax3.legend(loc="best")
    ax3.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    fig3.autofmt_xdate()
    p3 = output_dir / "wins_losses_cumulative_timeline.png"
    fig3.tight_layout()
    fig3.savefig(p3, dpi=160)
    plt.close(fig3)
    generated.append(p3)

    return generated


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "ejemplo.md"
    settings = AppSettings.load()

    messages = parse_ejemplo(path)

    parser = SignalParser(
        default_amount=settings.default_amount,
        signal_tz_offset_hours=settings.expected_utc_offset_hours,
        signal_timezone=settings.signal_timezone,
    )

    parsed_signals: list[SignalEvent] = []
    results: list[tuple[datetime, str]] = []

    for msg in messages:
        sig = parser.parse(msg.text, received_at_utc=msg.ts_local.replace(tzinfo=timezone.utc))
        if sig is not None:
            parsed_signals.append(
                SignalEvent(
                    ts_local=msg.ts_local,
                    channel=msg.channel,
                    asset=sig.asset,
                    side=sig.side,
                    expiry_minutes=sig.expiry_minutes,
                )
            )
            continue

        rk = classify_result(msg.text)
        if rk is not None:
            results.append((msg.ts_local, rk))

    # Emparejamiento FIFO por secuencia temporal (flujo del canal)
    pending = list(parsed_signals)
    resolved: list[ResolvedTrade] = []
    for _, rk in results:
        if not pending:
            break
        sig = pending.pop(0)
        resolved.append(ResolvedTrade(signal=sig, result_kind=rk))

    # Simulación PnL con la lógica actual (modo masaniello + cap)
    payout = settings.calc_payout_percent / 100.0
    auto_g1 = (1.0 + payout) / payout
    auto_g2 = auto_g1 * auto_g1
    g1_mult = settings.recovery_g1_mult if settings.recovery_g1_mult > 0 else auto_g1
    g2_mult = settings.recovery_g2_mult if settings.recovery_g2_mult > 0 else auto_g2

    session = MasanielloSessionState(
        n_ops=settings.masaniello_n_ops,
        w_needed=settings.masaniello_w_needed,
        base_balance=settings.masaniello_base_balance,
        payout_mult=1.0 + payout,
    )

    cap = round(max(0.01, settings.masaniello_base_balance * settings.max_trade_pct), 2)

    pnl_by_trade: list[float] = []
    drawdown_curve: list[float] = []
    trade_points: list[TradePoint] = []
    equity = 100.0  # requested account size
    peak = equity

    step_counts = {"WD": 0, "G1": 0, "G2": 0, "L": 0}
    asset_counter: dict[str, int] = {}

    for trade in resolved:
        step_counts[trade.result_kind] = step_counts.get(trade.result_kind, 0) + 1
        asset_counter[trade.signal.asset] = asset_counter.get(trade.signal.asset, 0) + 1

        entry = session.current_entry_stake()
        g1 = round(min(entry * g1_mult, cap), 2)
        g2 = round(min(entry * g2_mult, cap), 2)

        if trade.result_kind == "WD":
            pnl = round(entry * payout, 2)
            session.record_win()
        elif trade.result_kind == "G1":
            pnl = round(-entry + (g1 * payout), 2)
            session.record_win()
        elif trade.result_kind == "G2":
            pnl = round(-(entry + g1) + (g2 * payout), 2)
            session.record_win()
        else:
            pnl = round(-(entry + g1 + g2), 2)
            session.record_loss()

        pnl_by_trade.append(pnl)
        equity = round(equity + pnl, 2)
        peak = max(peak, equity)
        dd = round((peak - equity), 2)
        drawdown_curve.append(dd)
        trade_points.append(
            TradePoint(
                ts_local=trade.signal.ts_local,
                result_kind=trade.result_kind,
                pnl=pnl,
                equity_after=equity,
                drawdown_abs=dd,
            )
        )

    total = len(resolved)
    wins = step_counts["WD"] + step_counts["G1"] + step_counts["G2"]
    losses = step_counts["L"]
    win_rate = (wins / total * 100.0) if total else 0.0

    gross_profit = round(sum(v for v in pnl_by_trade if v > 0), 2)
    gross_loss = round(-sum(v for v in pnl_by_trade if v < 0), 2)
    net = round(sum(pnl_by_trade), 2)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    avg_trade = round(mean(pnl_by_trade), 2) if pnl_by_trade else 0.0
    max_dd = max(drawdown_curve) if drawdown_curve else 0.0
    roi = round((net / 100.0) * 100.0, 2)

    top_assets = sorted(asset_counter.items(), key=lambda kv: kv[1], reverse=True)[:8]

    print("=== REPORTE BACKTEST OFFLINE DESDE ejemplo.md ===")
    print(f"Mensajes totales detectados: {len(messages)}")
    print(f"Señales parseadas: {len(parsed_signals)}")
    print(f"Mensajes de resultado detectados: {len(results)}")
    print(f"Trades resueltos (señal->resultado): {total}")
    print(f"Señales sin resultado emparejado: {len(pending)}")
    print()
    print("-- Calidad de ejecución --")
    print(f"Win rate total: {win_rate:.2f}% ({wins}W / {losses}L)")
    print(
        "Distribución de cierre: "
        f"WD={step_counts['WD']} | G1={step_counts['G1']} | G2={step_counts['G2']} | L={step_counts['L']}"
    )
    print()
    print("-- Performance financiera simulada (cuenta inicial=100, modo=masaniello) --")
    print(f"Payout usado: {settings.calc_payout_percent:.2f}%")
    print(f"Cap por trade: {cap:.2f} ({settings.max_trade_pct*100:.1f}% de base {settings.masaniello_base_balance:.0f})")
    print(f"Profit bruto: {gross_profit:.2f}")
    print(f"Loss bruto: {gross_loss:.2f}")
    print(f"Neto: {net:.2f}")
    print(f"ROI sobre 100: {roi:.2f}%")
    print(f"Profit factor: {profit_factor:.2f}")
    print(f"Expectancy por trade: {avg_trade:.2f}")
    print(f"Max drawdown absoluto: {max_dd:.2f}")
    print()

    charts_dir = root / "runtime" / "charts"
    chart_files = _save_charts(charts_dir, trade_points, initial_equity=100.0)
    if chart_files:
        print("-- Graficas generadas --")
        for chart in chart_files:
            print(chart)
    else:
        print("-- Graficas --")
        print("No se generaron. Instala matplotlib para habilitar output visual.")
    print()
    print("-- Activos más frecuentes --")
    for asset, count in top_assets:
        print(f"{asset}: {count}")


if __name__ == "__main__":
    main()
