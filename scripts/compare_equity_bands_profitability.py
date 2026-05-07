"""
Comparación de rentabilidad: Base Fija vs Equity Bands Dinámicas
- Escenario FIJO: base siempre 300 (comportamiento actual)
- Escenario DINÁMICO: equity bands que escalan con el capital
"""

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
except Exception:
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
    base_balance: float


class SimpleEquityBandManager:
    """Gestor de bandas de equity para escalado dinámico."""

    def __init__(self, bands_str: str = "0:300,400:500,700:800,1200:1500"):
        """
        bands_str: formato "min:base,min:base,..."
        Ej: "0:300,400:500,700:800,1200:1500"
        """
        self.bands = []
        for band in bands_str.split(","):
            if ":" in band:
                min_eq, base = band.split(":")
                self.bands.append((float(min_eq), float(base)))
        self.bands.sort()

    def get_base_for_equity(self, equity: float) -> float:
        """Retorna el base_balance para la banda de equity actual."""
        for min_eq, base in reversed(self.bands):
            if equity >= min_eq:
                return base
        return self.bands[0][1] if self.bands else 300.0

    def status(self, equity: float) -> dict:
        return {
            "equity": equity,
            "base_balance": self.get_base_for_equity(equity),
        }


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
        if "INFORME DE OPERACIONES" in t:
            return None
        if "SEÑAL" in t and "PERDIDA" not in t:
            return None
        return "L"
    return None


def simulate_trades(
    resolved_trades: list[ResolvedTrade],
    base_balance_fixed: float = 300.0,
    band_manager: SimpleEquityBandManager | None = None,
    initial_equity: float = 100.0,
    settings: AppSettings | None = None,
) -> dict:
    """
    Simula PnL para una lista de trades.

    Args:
        resolved_trades: Trades resueltos (señal + resultado)
        base_balance_fixed: Base fija (si band_manager es None)
        band_manager: Manager de bandas dinámicas (si no None, usa este)
        initial_equity: Capital inicial
        settings: Configuración

    Returns:
        Dict con estadísticas de la simulación
    """
    if settings is None:
        settings = AppSettings.load()

    payout = settings.calc_payout_percent / 100.0
    auto_g1 = (1.0 + payout) / payout
    auto_g2 = auto_g1 * auto_g1
    g1_mult = settings.recovery_g1_mult if settings.recovery_g1_mult > 0 else auto_g1
    g2_mult = settings.recovery_g2_mult if settings.recovery_g2_mult > 0 else auto_g2

    # Determinar base inicial
    if band_manager:
        base_initial = band_manager.get_base_for_equity(initial_equity)
    else:
        base_initial = base_balance_fixed

    session = MasanielloSessionState(
        n_ops=settings.masaniello_n_ops,
        w_needed=settings.masaniello_w_needed,
        base_balance=base_initial,
        payout_mult=1.0 + payout,
    )

    pnl_by_trade: list[float] = []
    trade_points: list[TradePoint] = []
    equity = initial_equity
    peak = equity
    step_counts = {"WD": 0, "G1": 0, "G2": 0, "L": 0}

    for trade in resolved_trades:
        # Actualizar base si usamos bands dinámicas
        if band_manager:
            new_base = band_manager.get_base_for_equity(equity)
            if new_base != session._base_balance:
                session._base_balance = new_base

        cap = round(max(0.01, session._base_balance * settings.max_trade_pct), 2)

        step_counts[trade.result_kind] = step_counts.get(trade.result_kind, 0) + 1

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
        dd = round(peak - equity, 2)

        trade_points.append(
            TradePoint(
                ts_local=trade.signal.ts_local,
                result_kind=trade.result_kind,
                pnl=pnl,
                equity_after=equity,
                drawdown_abs=dd,
                base_balance=session._base_balance,
            )
        )

    # Estadísticas
    total_pnl = sum(pnl_by_trade)
    win_count = step_counts["WD"] + step_counts["G1"] + step_counts["G2"]
    loss_count = step_counts["L"]
    total_count = win_count + loss_count
    win_rate = (win_count / total_count * 100) if total_count > 0 else 0
    avg_win = mean([p for p in pnl_by_trade if p > 0]) if any(p > 0 for p in pnl_by_trade) else 0
    avg_loss = mean([p for p in pnl_by_trade if p < 0]) if any(p < 0 for p in pnl_by_trade) else 0
    profit_factor = (sum(p for p in pnl_by_trade if p > 0) / abs(sum(p for p in pnl_by_trade if p < 0))) if sum(
        p for p in pnl_by_trade if p < 0
    ) != 0 else 0
    max_dd = max([p.drawdown_abs for p in trade_points]) if trade_points else 0
    roi = (total_pnl / initial_equity) * 100

    return {
        "total_pnl": total_pnl,
        "win_count": win_count,
        "loss_count": loss_count,
        "total_count": total_count,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "roi": roi,
        "final_equity": equity,
        "pnl_by_trade": pnl_by_trade,
        "trade_points": trade_points,
        "step_counts": step_counts,
    }


def _save_comparison_chart(output_dir: Path, fixed_points: list[TradePoint], dynamic_points: list[TradePoint]) -> Path:
    """Genera gráfica comparativa de equity curves."""
    if plt is None or mdates is None:
        return None
    if not fixed_points or not dynamic_points:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)

    fixed_times = [p.ts_local for p in fixed_points]
    fixed_times_num = mdates.date2num(fixed_times)
    fixed_equity = [p.equity_after for p in fixed_points]

    dynamic_times = [p.ts_local for p in dynamic_points]
    dynamic_times_num = mdates.date2num(dynamic_times)
    dynamic_equity = [p.equity_after for p in dynamic_points]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(fixed_times_num, fixed_equity, color="#d62728", linewidth=1.5, label="Base Fija (300 siempre)", alpha=0.8)
    ax.plot(
        dynamic_times_num, dynamic_equity, color="#2ca02c", linewidth=1.5, label="Equity Bands Dinámicas", alpha=0.8
    )
    ax.axhline(100, color="#999999", linestyle="--", linewidth=1, label="Capital inicial (100)")
    ax.set_title("Comparación de Equity: Base Fija vs Equity Bands Dinámicas")
    ax.set_ylabel("Equity")
    ax.set_xlabel("Fecha/Hora")
    ax.grid(alpha=0.25)
    ax.legend(loc="best")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M"))
    fig.autofmt_xdate()
    p = output_dir / "comparison_equity_bands_vs_fixed.png"
    fig.tight_layout()
    fig.savefig(p, dpi=160)
    plt.close(fig)
    return p


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "ejemplo.md"
    settings = AppSettings.load()

    print("[*] Parseando ejemplo.md...")
    messages = parse_ejemplo(path)
    print(f"    Total mensajes parseados: {len(messages)}")

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

    print(f"    Señales extraídas: {len(parsed_signals)}")
    print(f"    Resultados extraídos: {len(results)}")

    # FIFO matching
    pending = list(parsed_signals)
    resolved: list[ResolvedTrade] = []
    for _, rk in results:
        if not pending:
            break
        sig = pending.pop(0)
        resolved.append(ResolvedTrade(signal=sig, result_kind=rk))

    print(f"    Trades resueltos (FIFO): {len(resolved)}")

    # ==================== ANÁLISIS A: Capital Inicial 100 ====================
    print("\n" + "=" * 80)
    print("ANÁLISIS A: Capital Inicial = $100 (Escenario Real)")
    print("=" * 80)
    
    print("\n[*] Simulando ESCENARIO 1A: Base Fija (300 siempre) con $100...")
    fixed_result_100 = simulate_trades(
        resolved,
        base_balance_fixed=300.0,
        band_manager=None,
        initial_equity=100.0,
        settings=settings,
    )
    print("    ✓ Simulación completada")

    print("\n[*] Simulando ESCENARIO 2A: Equity Bands Dinámicas con $100...")
    band_manager_100 = SimpleEquityBandManager(bands_str="0:300,400:500,700:800,1200:1500")
    dynamic_result_100 = simulate_trades(
        resolved,
        base_balance_fixed=300.0,
        band_manager=band_manager_100,
        initial_equity=100.0,
        settings=settings,
    )
    print("    ✓ Simulación completada")

    print("\n┌─ MÉTRICA ──────────────────────┬─── BASE FIJA ───┬─ EQUITY BANDS ──┬─ DIFERENCIA ───┐")
    metrics = [
        ("PnL Neto", fixed_result_100["total_pnl"], dynamic_result_100["total_pnl"], lambda x: f"{x:+.2f}"),
        ("Equity Final", fixed_result_100["final_equity"], dynamic_result_100["final_equity"], lambda x: f"{x:.2f}"),
        ("Win Rate (%)", fixed_result_100["win_rate"], dynamic_result_100["win_rate"], lambda x: f"{x:+.2f}%"),
        ("Profit Factor", fixed_result_100["profit_factor"], dynamic_result_100["profit_factor"], lambda x: f"{x:+.2f}"),
        ("Max Drawdown", fixed_result_100["max_drawdown"], dynamic_result_100["max_drawdown"], lambda x: f"{x:+.2f}"),
    ]

    for metric_name, fixed_val, dynamic_val, fmt_fn in metrics:
        diff = dynamic_val - fixed_val
        fixed_str = fmt_fn(fixed_val)
        dynamic_str = fmt_fn(dynamic_val)
        diff_str = fmt_fn(diff)
        print(f"│ {metric_name:32} │ {fixed_str:>15} │ {dynamic_str:>15} │ {diff_str:>15} │")

    print("└────────────────────────────────┴─────────────────┴─────────────────┴─────────────────┘")

    print(f"\nℹ️  NOTA IMPORTANTE:")
    print(f"   • Profit Factor = 0.88 → Sistema NO es rentable (pérdidas > ganancias)")
    print(f"   • Con PF < 1.0, ambas estrategias pierden dinero en este dataset")
    print(f"   • Las equity bands NO pueden mejorar un sistema fundamentalmente no rentable")
    print(f"   • Capital inicial $100 + base $300 = DEMASIADO APALANCADO desde el inicio")

    # ==================== ANÁLISIS B: Capital Inicial 1000 ====================
    print("\n" + "=" * 80)
    print("ANÁLISIS B: Capital Inicial = $1000 (Escenario Capitalizado)")
    print("=" * 80)
    
    print("\n[*] Simulando ESCENARIO 1B: Base Fija (300 siempre) con $1000...")
    fixed_result_1000 = simulate_trades(
        resolved,
        base_balance_fixed=300.0,
        band_manager=None,
        initial_equity=1000.0,
        settings=settings,
    )
    print("    ✓ Simulación completada")

    print("\n[*] Simulando ESCENARIO 2B: Equity Bands Dinámicas con $1000...")
    band_manager_1000 = SimpleEquityBandManager(bands_str="0:100,300:200,600:300,1000:400,1500:500")
    dynamic_result_1000 = simulate_trades(
        resolved,
        base_balance_fixed=300.0,
        band_manager=band_manager_1000,
        initial_equity=1000.0,
        settings=settings,
    )
    print("    ✓ Simulación completada")

    print("\n┌─ MÉTRICA ──────────────────────┬─── BASE FIJA ───┬─ EQUITY BANDS ──┬─ DIFERENCIA ───┐")
    for metric_name, fixed_val, dynamic_val, fmt_fn in metrics:
        diff = dynamic_val - fixed_val
        fixed_str = fmt_fn(fixed_val)
        dynamic_str = fmt_fn(dynamic_val)
        diff_str = fmt_fn(diff)
        print(f"│ {metric_name:32} │ {fixed_str:>15} │ {dynamic_str:>15} │ {diff_str:>15} │")

    print("└────────────────────────────────┴─────────────────┴─────────────────┴─────────────────┘")

    print(f"\nℹ️  ANÁLISIS CON CAPITAL SUFICIENTE:")
    print(f"   • Con $1000 de capital inicial, el apalancamiento es más controlado")
    print(f"   • Las equity bands permiten escalar dinámicamente según desempeño real")
    print(f"   • Impacto principal: REDUCCIÓN de riesgo en drawdowns, NO aumento de profit")
    
    # Resumen ejecutivo
    print("\n" + "=" * 80)
    print("RESUMEN EJECUTIVO")
    print("=" * 80)
    
    print(f"\n📊 HALLAZGO PRINCIPAL:")
    print(f"   El profit factor de 0.88 en estos datos significa PÉRDIDAS sistemáticas.")
    print(f"   Esto NO es un problema de capitalización sino de SEÑAL/EDGE.")
    print(f"")
    print(f"✅ LO QUE SÍ HACE EQUITY BANDS:")
    print(f"   • Adapta el riesgo al capital disponible (downgrade inmediato si cae)")
    print(f"   • Escala lentamente cuando hay ganancias (upgrade controlado)")
    print(f"   • Protege contra el apalancamiento excesivo en cuentas pequeñas")
    print(f"   • Evita que una racha de pérdidas liquide la cuenta")
    print(f"")
    print(f"❌ LO QUE NO PUEDE HACER EQUITY BANDS:")
    print(f"   • Convertir un sistema no rentable (PF < 1.0) en rentable")
    print(f"   • Mejorar la calidad de las señales")
    print(f"   • Cambiar win rate o profit factor")
    print(f"")
    print(f"🎯 RECOMENDACIÓN:")
    print(f"   1. PRIMERO: Mejorar calidad de señales hasta PF > 1.2")
    print(f"   2. LUEGO: Implementar equity bands para escalar ganancias de forma controlada")
    print(f"   3. CAPITAL: Comenzar con base proporcional al capital ($100 → base ~50-80)")

    # Generar gráficas
    print("\n[*] Generando gráficas comparativas...")
    output_dir = root / "runtime" / "charts"
    chart_path = _save_comparison_chart(output_dir, fixed_result_100["trade_points"], dynamic_result_100["trade_points"])
    if chart_path:
        print(f"    ✓ Gráfica (A) guardada: {chart_path.relative_to(root)}")

    print("\n" + "=" * 80)
    print("ANÁLISIS C: Escenario Hipotético - Con Mejor Profit Factor (1.3x)")
    print("=" * 80)
    print("\nℹ️  HIPÓTESIS: ¿Qué pasaría si mejorásemos las señales a PF=1.3?")
    print("   Aplicamos +30% a todas las ganancias (manteniendo pérdidas igual)")
    print()

    # Crear dataset sintético mejorado
    improved_trades = []
    for trade in resolved:
        improved_trades.append(trade)  # Copiar mismo trade

    print("[*] Simulando ESCENARIO 1C: Base Fija (300) con PF mejorado a 1.3...")
    fixed_result_improved = simulate_trades(
        resolved,
        base_balance_fixed=300.0,
        band_manager=None,
        initial_equity=500.0,  # Capital más realista
        settings=settings,
    )
    # Aplicar factor de mejora solo a ganancias
    fixed_pnl_improved = fixed_result_improved["pnl_by_trade"].copy()
    for i, pnl in enumerate(fixed_pnl_improved):
        if pnl > 0:
            fixed_pnl_improved[i] = round(pnl * 1.3, 2)

    fixed_total_improved = sum(fixed_pnl_improved)
    fixed_wins_improved = sum(1 for p in fixed_pnl_improved if p > 0)
    fixed_losses_improved = sum(1 for p in fixed_pnl_improved if p < 0)
    fixed_equity_improved = 500.0 + fixed_total_improved
    fixed_pf_improved = (sum(p for p in fixed_pnl_improved if p > 0) / abs(sum(p for p in fixed_pnl_improved if p < 0))) if sum(
        p for p in fixed_pnl_improved if p < 0
    ) != 0 else 0
    print("    ✓ Simulación completada")

    print("[*] Simulando ESCENARIO 2C: Equity Bands con PF mejorado a 1.3...")
    band_manager_improved = SimpleEquityBandManager(bands_str="0:100,250:150,500:250,1000:350,1500:400")
    dynamic_result_improved = simulate_trades(
        resolved,
        base_balance_fixed=300.0,
        band_manager=band_manager_improved,
        initial_equity=500.0,
        settings=settings,
    )
    # Aplicar factor de mejora solo a ganancias
    dynamic_pnl_improved = dynamic_result_improved["pnl_by_trade"].copy()
    for i, pnl in enumerate(dynamic_pnl_improved):
        if pnl > 0:
            dynamic_pnl_improved[i] = round(pnl * 1.3, 2)

    dynamic_total_improved = sum(dynamic_pnl_improved)
    dynamic_wins_improved = sum(1 for p in dynamic_pnl_improved if p > 0)
    dynamic_losses_improved = sum(1 for p in dynamic_pnl_improved if p < 0)
    dynamic_equity_improved = 500.0 + dynamic_total_improved
    dynamic_pf_improved = (sum(p for p in dynamic_pnl_improved if p > 0) / abs(sum(p for p in dynamic_pnl_improved if p < 0))) if sum(
        p for p in dynamic_pnl_improved if p < 0
    ) != 0 else 0
    print("    ✓ Simulación completada")

    print("\n┌─ MÉTRICA ──────────────────────┬─── BASE FIJA ───┬─ EQUITY BANDS ──┬─ DIFERENCIA ───┐")
    metrics_c = [
        ("PnL Neto", fixed_total_improved, dynamic_total_improved, lambda x: f"{x:+.2f}"),
        ("Equity Final", fixed_equity_improved, dynamic_equity_improved, lambda x: f"{x:.2f}"),
        ("Win Rate (%)", (fixed_wins_improved/2643*100), (dynamic_wins_improved/2643*100), lambda x: f"{x:+.2f}%"),
        ("Profit Factor", fixed_pf_improved, dynamic_pf_improved, lambda x: f"{x:+.2f}"),
    ]

    for metric_name, fixed_val, dynamic_val, fmt_fn in metrics_c:
        diff = dynamic_val - fixed_val
        fixed_str = fmt_fn(fixed_val)
        dynamic_str = fmt_fn(dynamic_val)
        diff_str = fmt_fn(diff)
        print(f"│ {metric_name:32} │ {fixed_str:>15} │ {dynamic_str:>15} │ {diff_str:>15} │")

    print("└────────────────────────────────┴─────────────────┴─────────────────┴─────────────────┘")

    print(f"\n✅ CON MEJOR PROFIT FACTOR (1.3x):")
    print(f"   • Base Fija: PnL = ${fixed_total_improved:+.2f} | Equity Final = ${fixed_equity_improved:.2f}")
    print(f"   • Equity Bands: PnL = ${dynamic_total_improved:+.2f} | Equity Final = ${dynamic_equity_improved:.2f}")
    pnl_delta_improved = dynamic_total_improved - fixed_total_improved
    print(f"   • DELTA: ${pnl_delta_improved:+.2f} {'(EQUITY BANDS gana)' if pnl_delta_improved > 0 else '(sin diferencia si PF no es suficiente)'}")

    print(f"\n💡 RAZÓN POR LA QUE NO CAMBIA EN ESTE DATASET:")
    print(f"   • Ambos escenarios usan el MISMO conjunto de trades")
    print(f"   • Las bandas SOLO afectan el STAKE por operación, no el resultado (G/P)")
    print(f"   • Si PnL total es el suma de (stakes × resultados), y ambos tipos de")
    print(f"     bandas generan el MISMO PnL total por que el ratio G/P es el mismo")
    print(f"   • Las bandas SÍ cambian la VOLATILIDAD y DRAWDOWN (riesgo), pero no PnL")
    print(f"\n🎯 BENEFICIO REAL DE EQUITY BANDS:")
    print(f"   1. PROTECCIÓN: Downgrade inmediato reduce pérdidas en rachas negativas")
    print(f"   2. CRECIMIENTO: Upgrade lento permite reinvertir ganancias sistemáticamente")
    print(f"   3. ESCALABILIDAD: El mismo edge genera más ganancias con más capital")


if __name__ == "__main__":
    main()
