"""
Simulacion monetaria real sobre el historico de mensajes:

- Una sesion = 6 operaciones reales del canal.
- En cada sesion, el bot se detiene al conseguir 2 victorias.
- Se usa la formula de stakes Masaniello implementada en el repo.
- Las apuestas se calculan sobre una banca base fija de $300 para no escalar riesgo.
- El dia se detiene al alcanzar +$60.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sim_objetivo_60_por_dia import (
    CAPITAL_INICIAL,
    HISTORY,
    META_DIARIA,
    N_OPS,
    PAYOUT_MULT,
    W_NEEDED,
    group_sessions,
    masaniello_stake,
    parse_outcomes,
)


BASE_STAKE_BALANCE = 300.0


@dataclass
class SessionMoney:
    index: int
    day: str
    start_time: str
    consumed_results: list[str]
    stake_trace: list[float]
    pnl: float
    wins: int
    losses: int


def simulate_money() -> tuple[list[SessionMoney], dict[str, dict[str, object]]]:
    outcomes = parse_outcomes(HISTORY)
    sessions = group_sessions(outcomes, N_OPS)

    balance = CAPITAL_INICIAL
    min_balance = balance
    peak_balance = balance
    max_drawdown = 0.0

    session_rows: list[SessionMoney] = []
    daily = defaultdict(lambda: {
        "sessions_traded": 0,
        "pnl": 0.0,
        "meta_hit": False,
        "meta_time": None,
        "start_balance": None,
        "end_balance": None,
        "min_balance": None,
        "ops_used": 0,
        "session_pnls": [],
    })

    for session_index, chunk in enumerate(sessions, start=1):
        day = chunk[0].timestamp.strftime("%d/%m/%Y")
        row = daily[day]
        if row["start_balance"] is None:
            row["start_balance"] = round(balance, 2)
            row["min_balance"] = round(balance, 2)

        if row["meta_hit"]:
            continue

        wins = 0
        losses = 0
        pnl = 0.0
        consumed_results: list[str] = []
        stake_trace: list[float] = []
        session_end_time = chunk[0].timestamp

        for outcome in chunk:
            wins_needed = W_NEEDED - wins
            ops_left = N_OPS - (wins + losses)
            if wins_needed <= 0 or wins_needed > ops_left:
                break

            stake = masaniello_stake(BASE_STAKE_BALANCE, losses, wins, N_OPS, W_NEEDED, PAYOUT_MULT)
            if balance < stake:
                raise RuntimeError(f"Balance insuficiente para stake ${stake:.2f} en sesion {session_index}")

            stake_trace.append(stake)
            consumed_results.append(outcome.result)
            session_end_time = outcome.timestamp

            if outcome.is_win:
                wins += 1
                pnl += stake * (PAYOUT_MULT - 1)
                balance += stake * (PAYOUT_MULT - 1)
            else:
                losses += 1
                pnl -= stake
                balance -= stake

            balance = round(balance, 2)
            min_balance = min(min_balance, balance)
            peak_balance = max(peak_balance, balance)
            max_drawdown = max(max_drawdown, round(peak_balance - balance, 2))
            row["min_balance"] = min(row["min_balance"], balance)
            row["ops_used"] += 1

            if wins >= W_NEEDED:
                break

        pnl = round(pnl, 2)
        row["sessions_traded"] += 1
        row["pnl"] = round(row["pnl"] + pnl, 2)
        row["session_pnls"].append(pnl)
        row["end_balance"] = round(balance, 2)

        session_rows.append(
            SessionMoney(
                index=session_index,
                day=day,
                start_time=chunk[0].timestamp.strftime("%H:%M:%S"),
                consumed_results=consumed_results,
                stake_trace=stake_trace,
                pnl=pnl,
                wins=wins,
                losses=losses,
            )
        )

        if row["pnl"] >= META_DIARIA and not row["meta_hit"]:
            row["meta_hit"] = True
            row["meta_time"] = session_end_time.strftime("%H:%M:%S")

    for row in daily.values():
        if row["end_balance"] is None and row["start_balance"] is not None:
            row["end_balance"] = row["start_balance"]

    stats = {
        "final_balance": round(balance, 2),
        "min_balance": round(min_balance, 2),
        "peak_balance": round(peak_balance, 2),
        "max_drawdown": round(max_drawdown, 2),
    }
    return session_rows, daily, stats


def main() -> None:
    sessions, daily, stats = simulate_money()
    total_days = len(daily)
    meta_days = sum(1 for row in daily.values() if row["meta_hit"])
    misses = [
        (day, row)
        for day, row in daily.items()
        if not row["meta_hit"]
    ]

    print("=" * 76)
    print("SIMULACION MONETARIA REAL: 2 GANADAS POR SESION Y STOP AL LLEGAR A $60")
    print("=" * 76)
    print(f"Banca inicial: ${CAPITAL_INICIAL:.2f}")
    print(f"Banca base para stakes: ${BASE_STAKE_BALANCE:.2f}")
    print(f"Meta diaria: ${META_DIARIA:.2f}")
    print()

    print("Stakes Masaniello fijos sobre $300:")
    print(f"  Estado 0L/0W: ${masaniello_stake(BASE_STAKE_BALANCE, 0, 0, N_OPS, W_NEEDED, PAYOUT_MULT):.2f}")
    print(f"  Estado 1L/0W: ${masaniello_stake(BASE_STAKE_BALANCE, 1, 0, N_OPS, W_NEEDED, PAYOUT_MULT):.2f}")
    print(f"  Estado 2L/0W: ${masaniello_stake(BASE_STAKE_BALANCE, 2, 0, N_OPS, W_NEEDED, PAYOUT_MULT):.2f}")
    print()

    print("Resumen global:")
    print(f"  Dias analizados: {total_days}")
    print(f"  Dias que llegaron a $60: {meta_days}")
    print(f"  Dias que no llegaron a $60: {total_days - meta_days}")
    print(f"  Balance final: ${stats['final_balance']:.2f}")
    print(f"  Balance minimo intradia: ${stats['min_balance']:.2f}")
    print(f"  Balance maximo: ${stats['peak_balance']:.2f}")
    print(f"  Drawdown maximo observado: ${stats['max_drawdown']:.2f}")
    print()

    print("Primeros 12 dias:")
    for day in sorted(daily.keys(), key=lambda value: datetime.strptime(value, "%d/%m/%Y"))[:12]:
        row = daily[day]
        print(
            f"  {day} | traded={row['sessions_traded']} | pnl=${row['pnl']:.2f} | "
            f"meta={row['meta_hit']} | hora_meta={row['meta_time'] or '--:--:--'} | "
            f"saldo_ini=${row['start_balance']:.2f} | saldo_fin=${row['end_balance']:.2f}"
        )
    print()

    print("Dias que no llegaron a $60:")
    for day, row in sorted(misses, key=lambda item: datetime.strptime(item[0], "%d/%m/%Y")):
        print(
            f"  {day} | pnl=${row['pnl']:.2f} | sesiones={row['sessions_traded']} | "
            f"saldo_ini=${row['start_balance']:.2f} | saldo_fin=${row['end_balance']:.2f}"
        )
    print()

    print("Primeras 10 sesiones operadas:")
    for item in sessions[:10]:
        print(
            f"  #{item.index:03d} {item.day} {item.start_time} | pnl=${item.pnl:.2f} | "
            f"path={item.consumed_results} | stakes={[round(x, 2) for x in item.stake_trace]}"
        )
    print()

    print("Sesiones con menor ganancia:")
    for item in sorted(sessions, key=lambda row: row.pnl)[:10]:
        print(
            f"  #{item.index:03d} {item.day} {item.start_time} | pnl=${item.pnl:.2f} | "
            f"path={item.consumed_results}"
        )


if __name__ == "__main__":
    main()