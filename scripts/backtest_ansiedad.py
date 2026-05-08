from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "ejemplo.md"
OUT_JSON = ROOT / "runtime" / "backtest_ansiedad.json"

PAYOUT_MULT = 1.92
NET_PAYOUT = 0.92
G1_MULT = PAYOUT_MULT / NET_PAYOUT
G2_MULT = G1_MULT * G1_MULT
TOTAL_MULT = 1.0 + G1_MULT + G2_MULT
BASE = 300.0
CAP_PCT = 0.10
CAP_TOTAL = BASE * CAP_PCT


@dataclass
class Outcome:
    ts: datetime
    result: str  # WD | G1 | G2 | L


@dataclass
class SimMetrics:
    name: str
    final_balance: float
    pnl: float
    max_drawdown: float
    min_balance: float
    traded_signals: int
    total_signals: int
    trade_ratio_pct: float
    broke: bool


def parse_outcomes(path: Path) -> list[Outcome]:
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

    outcomes: list[Outcome] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        dm = date_pat.match(line)
        if not dm:
            continue
        rm = res_pat.search(line)
        if not rm:
            continue

        ts = datetime.strptime(" ".join(dm.groups()), "%d/%m/%Y %H:%M:%S")
        raw = rm.group(1).lower()
        label = next((v for k, v in label_map.items() if raw.startswith(k)), "?")
        if label != "?":
            outcomes.append(Outcome(ts=ts, result=label))
    return outcomes


_TABLES: dict[tuple[int, int], dict[tuple[int, int], float]] = {}


def _fwd(ops: int, wins: int, pm: float) -> float:
    if wins <= 0:
        return 1.0
    if wins > ops:
        return 0.0
    if wins == ops:
        return pm ** ops
    pw = _fwd(ops - 1, wins - 1, pm)
    pl = _fwd(ops - 1, wins, pm)
    den = pw + (pm - 1.0) * pl
    return (pm * pw * pl / den) if den else 0.0


def masaniello_raw(losses: int, wins: int, n: int, w: int, pm: float) -> float:
    ops_left = n - (losses + wins)
    wins_left = w - wins
    if ops_left <= 0 or wins_left <= 0 or wins_left > ops_left:
        return 0.0
    pw = _fwd(ops_left - 1, wins_left - 1, pm)
    pl = _fwd(ops_left - 1, wins_left, pm)
    den = pw + (pm - 1.0) * pl
    if not den:
        return 1.0
    return max(0.001, min(1.0 - pm * pw / den, 1.0))


def get_table(n: int, w: int) -> dict[tuple[int, int], float]:
    key = (n, w)
    if key not in _TABLES:
        tab: dict[tuple[int, int], float] = {}
        for losses in range(n):
            for wins in range(n):
                if losses + wins < n:
                    tab[(losses, wins)] = masaniello_raw(losses, wins, n, w, PAYOUT_MULT)
        _TABLES[key] = tab
    return _TABLES[key]


def pnl_from_outcome(result: str, entry: float) -> float:
    g1 = entry * G1_MULT
    g2 = entry * G2_MULT
    if result == "WD":
        return round(entry * NET_PAYOUT, 2)
    if result == "G1":
        return round(g1 * NET_PAYOUT - entry, 2)
    if result == "G2":
        return round(g2 * NET_PAYOUT - entry - g1, 2)
    return round(-(entry + g1 + g2), 2)


def cap_entry_total(entry_raw: float) -> float:
    entry_max = CAP_TOTAL / TOTAL_MULT
    return round(max(0.01, min(entry_raw, entry_max)), 2)


def cap_entry_per_step(entry_raw: float) -> float:
    cap = round(CAP_TOTAL, 2)
    entry = min(entry_raw, cap)
    return round(max(0.01, entry), 2)


def sim_system_a(outcomes: list[Outcome]) -> tuple[SimMetrics, list[float], list[float], list[int]]:
    # Sistema A: Masaniello 12/4 continuo, cap por paso (agresivo)
    table = get_table(12, 4)
    balance = BASE
    peak = BASE
    min_bal = BASE
    max_dd = 0.0

    cycle_wins = 0
    cycle_losses = 0
    cycle_used = 0

    equity: list[float] = []
    pnl_series: list[float] = []
    traded_flags: list[int] = []

    for o in outcomes:
        raw = table.get((cycle_losses, cycle_wins), 0.0) * BASE
        entry = cap_entry_per_step(raw)

        # Cap por paso en A
        cap = round(CAP_TOTAL, 2)
        e = entry
        g1 = round(min(e * G1_MULT, cap), 2)
        g2 = round(min(e * G2_MULT, cap), 2)

        if o.result == "WD":
            pnl = round(e * NET_PAYOUT, 2)
            cycle_wins += 1
        elif o.result == "G1":
            pnl = round(g1 * NET_PAYOUT - e, 2)
            cycle_wins += 1
        elif o.result == "G2":
            pnl = round(g2 * NET_PAYOUT - e - g1, 2)
            cycle_wins += 1
        else:
            pnl = round(-(e + g1 + g2), 2)
            cycle_losses += 1

        balance = round(balance + pnl, 2)
        cycle_used += 1

        peak = max(peak, balance)
        min_bal = min(min_bal, balance)
        max_dd = max(max_dd, round(peak - balance, 2))

        equity.append(balance)
        pnl_series.append(pnl)
        traded_flags.append(1)

        # cierre de ciclo masaniello
        if cycle_wins >= 4 or cycle_used >= 12:
            cycle_wins = 0
            cycle_losses = 0
            cycle_used = 0

    m = SimMetrics(
        name="Sistema A",
        final_balance=round(balance, 2),
        pnl=round(balance - BASE, 2),
        max_drawdown=round(max_dd, 2),
        min_balance=round(min_bal, 2),
        traded_signals=len(outcomes),
        total_signals=len(outcomes),
        trade_ratio_pct=100.0,
        broke=balance <= 0,
    )
    return m, equity, pnl_series, traded_flags


def _session_index_2h(ts: datetime) -> int:
    # 12 sesiones por dia de 2 horas cada una
    return ts.hour // 2


def sim_system_b(outcomes: list[Outcome]) -> tuple[SimMetrics, list[float], list[float], list[int], dict[str, int]]:
    # Sistema B: 12 sesiones/dia, max 6 mensajes/sesion, TP=2 wins, SL=1 L,
    # escudo: tras L previo, siguiente entrada = $1
    table = get_table(6, 2)
    balance = BASE
    peak = BASE
    min_bal = BASE
    max_dd = 0.0

    shield_next = False
    traded = 0

    day_session_state: dict[tuple[str, int], dict[str, int | bool]] = {}

    equity: list[float] = []
    pnl_series: list[float] = []
    traded_flags: list[int] = []

    sessions_blocked_sl = 0
    sessions_tp_hit = 0
    skipped_by_rules = 0

    for o in outcomes:
        day = o.ts.strftime("%d/%m/%Y")
        sess = _session_index_2h(o.ts)
        key = (day, sess)

        if key not in day_session_state:
            day_session_state[key] = {
                "wins": 0,
                "losses": 0,
                "used": 0,
                "closed": False,
            }

        st = day_session_state[key]
        if st["closed"]:
            skipped_by_rules += 1
            equity.append(balance)
            pnl_series.append(0.0)
            traded_flags.append(0)
            continue

        if int(st["used"]) >= 6:
            st["closed"] = True
            skipped_by_rules += 1
            equity.append(balance)
            pnl_series.append(0.0)
            traded_flags.append(0)
            continue

        wins = int(st["wins"])
        losses = int(st["losses"])

        raw = table.get((losses, wins), 0.0) * BASE
        entry = cap_entry_total(raw)

        if shield_next:
            entry = 1.0
            shield_next = False

        pnl = pnl_from_outcome(o.result, entry)
        balance = round(balance + pnl, 2)

        traded += 1
        st["used"] = int(st["used"]) + 1

        if o.result == "L":
            st["losses"] = int(st["losses"]) + 1
            st["closed"] = True
            sessions_blocked_sl += 1
            shield_next = True
        else:
            st["wins"] = int(st["wins"]) + 1
            if int(st["wins"]) >= 2:
                st["closed"] = True
                sessions_tp_hit += 1

        peak = max(peak, balance)
        min_bal = min(min_bal, balance)
        max_dd = max(max_dd, round(peak - balance, 2))

        equity.append(balance)
        pnl_series.append(pnl)
        traded_flags.append(1)

    m = SimMetrics(
        name="Sistema B",
        final_balance=round(balance, 2),
        pnl=round(balance - BASE, 2),
        max_drawdown=round(max_dd, 2),
        min_balance=round(min_bal, 2),
        traded_signals=traded,
        total_signals=len(outcomes),
        trade_ratio_pct=round((traded / len(outcomes)) * 100, 2),
        broke=balance <= 0,
    )
    extra = {
        "sessions_stop_loss": sessions_blocked_sl,
        "sessions_take_profit": sessions_tp_hit,
        "signals_skipped_by_rules": skipped_by_rules,
    }
    return m, equity, pnl_series, traded_flags, extra


def survival_analysis(eq_a: list[float], eq_b: list[float]) -> dict[str, int]:
    # Cuenta episodios donde A cruza a quiebra (<=0) mientras B sigue vivo (>0)
    episodes = 0
    points = 0
    in_episode = False
    for ba, bb in zip(eq_a, eq_b):
        condition = ba <= 0 < bb
        if condition:
            points += 1
            if not in_episode:
                episodes += 1
                in_episode = True
        else:
            in_episode = False
    return {"saved_break_episodes": episodes, "saved_break_points": points}


def stress_metrics(pnl_series: list[float], traded_flags: list[int]) -> dict[str, float]:
    max_consec_trade = 0
    cur_trade = 0
    max_consec_loss = 0
    cur_loss = 0
    for pnl, tr in zip(pnl_series, traded_flags):
        if tr:
            cur_trade += 1
            max_consec_trade = max(max_consec_trade, cur_trade)
            if pnl < 0:
                cur_loss += 1
                max_consec_loss = max(max_consec_loss, cur_loss)
            else:
                cur_loss = 0
        else:
            cur_trade = 0
            cur_loss = 0
    return {
        "max_consecutive_trades": float(max_consec_trade),
        "max_consecutive_losses": float(max_consec_loss),
    }


def main() -> None:
    outcomes = parse_outcomes(HISTORY)
    m_a, eq_a, pnl_a, tr_a = sim_system_a(outcomes)
    m_b, eq_b, pnl_b, tr_b, extra_b = sim_system_b(outcomes)

    surv = survival_analysis(eq_a, eq_b)
    stress_a = stress_metrics(pnl_a, tr_a)
    stress_b = stress_metrics(pnl_b, tr_b)

    report = {
        "dataset": {
            "signals": len(outcomes),
            "start": outcomes[0].ts.isoformat() if outcomes else None,
            "end": outcomes[-1].ts.isoformat() if outcomes else None,
            "base": BASE,
            "payout_mult": PAYOUT_MULT,
        },
        "system_a": m_a.__dict__ | stress_a,
        "system_b": m_b.__dict__ | stress_b | extra_b,
        "survival": surv,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== RESULTADOS BACKTEST ANSIEDAD ===")
    print(f"Sistema A | Final=${m_a.final_balance:.2f} | PnL=${m_a.pnl:+.2f} | MaxDD=${m_a.max_drawdown:.2f}")
    print(f"Sistema B | Final=${m_b.final_balance:.2f} | PnL=${m_b.pnl:+.2f} | MaxDD=${m_b.max_drawdown:.2f}")
    print(
        "Supervivencia | "
        f"episodios salvados={surv['saved_break_episodes']} | "
        f"puntos salvados={surv['saved_break_points']}"
    )
    print(
        "Exposición | "
        f"A operó {m_a.traded_signals}/{m_a.total_signals} ({m_a.trade_ratio_pct:.2f}%) | "
        f"B operó {m_b.traded_signals}/{m_b.total_signals} ({m_b.trade_ratio_pct:.2f}%)"
    )
    print(f"JSON: {OUT_JSON}")


if __name__ == "__main__":
    main()
