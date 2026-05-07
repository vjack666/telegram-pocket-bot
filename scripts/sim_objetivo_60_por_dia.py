"""
Simula el historico real del canal con esta regla operativa:

- Una sesion = 6 mensajes (senales ya resueltas con hasta 2 gales incluidos)
- Se busca ganar 2 operaciones por sesion
- En cuanto la sesion llega a 2 victorias, el bot se detiene hasta la siguiente sesion
- La meta diaria es +$60

Se usa la logica de stake de Masaniello 6/2, pero la sesion se corta al cumplir W=2.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "ejemplo.md"

CAPITAL_INICIAL = 300.0
META_DIARIA = 60.0
N_OPS = 6
W_NEEDED = 2
PAYOUT_MULT = 1.92
TARGET_SESSION_WIN = 20.0


@dataclass
class Outcome:
    timestamp: datetime
    result: str
    is_win: bool


@dataclass
class SessionResult:
    index: int
    day: str
    start_ts: datetime
    end_ts: datetime
    wins: int
    losses: int
    used_ops: int
    consumed_results: list[str]
    raw_results: list[str]
    pnl: float
    target_hit: bool


def _forward_prob(ops_left: int, wins_needed: int, payout_mult: float) -> float:
    if wins_needed <= 0:
        return 1.0
    if wins_needed > ops_left:
        return 0.0
    if wins_needed == ops_left:
        return payout_mult ** ops_left

    p_if_win = _forward_prob(ops_left - 1, wins_needed - 1, payout_mult)
    p_if_lose = _forward_prob(ops_left - 1, wins_needed, payout_mult)
    denom = p_if_win + (payout_mult - 1) * p_if_lose
    if denom == 0:
        return 0.0
    return payout_mult * p_if_win * p_if_lose / denom


def masaniello_stake(balance: float, losses_so_far: int, wins_so_far: int, n: int, w: int, payout_mult: float) -> float:
    ops_done = losses_so_far + wins_so_far
    ops_left = n - ops_done
    wins_left = w - wins_so_far

    if ops_left <= 0 or wins_left <= 0 or wins_left > ops_left:
        return 0.0

    new_wins_left = wins_left - 1
    new_ops_left = ops_left - 1
    if new_wins_left <= 0:
        p_win_fwd = 1.0
    elif new_wins_left > new_ops_left:
        p_win_fwd = 0.0
    else:
        p_win_fwd = _forward_prob(new_ops_left, new_wins_left, payout_mult)

    new_wins_left2 = wins_left
    new_ops_left2 = ops_left - 1
    if new_wins_left2 <= 0:
        p_lose_fwd = 1.0
    elif new_wins_left2 > new_ops_left2:
        p_lose_fwd = 0.0
    else:
        p_lose_fwd = _forward_prob(new_ops_left2, new_wins_left2, payout_mult)

    denom = p_win_fwd + (payout_mult - 1) * p_lose_fwd
    if denom == 0:
        return balance

    stake = balance * (1 - payout_mult * p_win_fwd / denom)
    return round(max(0.01, min(stake, balance)), 2)


def parse_outcomes(path: Path) -> list[Outcome]:
    date_pat = re.compile(r"^\[(\d{2}/\d{2}/\d{4}) (\d{2}:\d{2}:\d{2})\]")
    res_pat = re.compile(
        r"VICTORIA DIRECTA|VICTORIA EN 1.*MARTINGALA|VICTORIA EN 2.*MARTINGALA|P[ÉE]RDIDA",
        re.IGNORECASE,
    )

    outcomes: list[Outcome] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        ts_match = date_pat.match(line)
        res_match = res_pat.search(line)
        if not ts_match or not res_match:
            continue

        timestamp = datetime.strptime(
            f"{ts_match.group(1)} {ts_match.group(2)}",
            "%d/%m/%Y %H:%M:%S",
        )
        txt = res_match.group(0).upper()
        if "VICTORIA" in txt:
            if "DIRECTA" in txt:
                result = "WD"
            elif "1" in txt:
                result = "G1"
            else:
                result = "G2"
            is_win = True
        else:
            result = "L"
            is_win = False
        outcomes.append(Outcome(timestamp=timestamp, result=result, is_win=is_win))
    return outcomes


def group_sessions(outcomes: list[Outcome], size: int = 6) -> list[list[Outcome]]:
    sessions = []
    for index in range(0, len(outcomes), size):
        chunk = outcomes[index:index + size]
        if len(chunk) == size:
            sessions.append(chunk)
    return sessions


def simulate() -> tuple[list[SessionResult], dict[str, dict[str, float | int | bool | str | None]]]:
    outcomes = parse_outcomes(HISTORY)
    sessions = group_sessions(outcomes, N_OPS)

    session_results: list[SessionResult] = []
    daily = defaultdict(lambda: {
        "sessions": 0,
        "wins": 0,
        "losses": 0,
        "pnl": 0.0,
        "meta_hit": False,
        "ops_used": 0,
        "meta_hit_at": None,
    })

    for session_index, chunk in enumerate(sessions, start=1):
        wins = 0
        losses = 0
        used_ops = 0
        consumed_results: list[str] = []

        for outcome in chunk:
            wins_needed = W_NEEDED - wins
            ops_left = N_OPS - (wins + losses)
            if wins_needed <= 0 or wins_needed > ops_left:
                break

            stake = masaniello_stake(CAPITAL_INICIAL, losses, wins, N_OPS, W_NEEDED, PAYOUT_MULT)
            if outcome.is_win:
                wins += 1
            else:
                losses += 1
            used_ops += 1
            consumed_results.append(outcome.result)

            if wins >= W_NEEDED:
                break

        day = chunk[0].timestamp.strftime("%d/%m/%Y")
        target_hit = wins >= W_NEEDED
        pnl = TARGET_SESSION_WIN if target_hit else 0.0
        raw_results = [item.result for item in chunk]
        session_results.append(
            SessionResult(
                index=session_index,
                day=day,
                start_ts=chunk[0].timestamp,
                end_ts=chunk[-1].timestamp,
                wins=wins,
                losses=losses,
                used_ops=used_ops,
                consumed_results=consumed_results,
                raw_results=raw_results,
                pnl=pnl,
                target_hit=target_hit,
            )
        )

        daily_row = daily[day]
        daily_row["sessions"] += 1
        daily_row["wins"] += 1 if target_hit else 0
        daily_row["losses"] += 0 if target_hit else 1
        daily_row["pnl"] = round(daily_row["pnl"] + pnl, 2)
        daily_row["ops_used"] += used_ops
        if daily_row["pnl"] >= META_DIARIA and not daily_row["meta_hit"]:
            daily_row["meta_hit"] = True
            daily_row["meta_hit_at"] = chunk[min(used_ops, len(chunk)) - 1].timestamp.strftime("%H:%M:%S")

    return session_results, daily


def main() -> None:
    sessions, daily = simulate()

    total_days = len(daily)
    meta_days = sum(1 for row in daily.values() if row["meta_hit"])
    bad_sessions = [s for s in sessions if not s.target_hit]
    first_session = sessions[0]

    print("=" * 72)
    print("SIMULACION: GANAR 2 OPERACIONES Y PARAR HASTA LA PROXIMA SESION")
    print("=" * 72)
    print(f"Capital inicial: ${CAPITAL_INICIAL:.2f}")
    print(f"Meta diaria: ${META_DIARIA:.2f}")
    print(f"Sesion objetivo: {W_NEEDED} victorias de {N_OPS} mensajes")
    print(f"Target por sesion ganada: ${TARGET_SESSION_WIN:.2f}")
    print()
    print("Chequeo rapido del modelo:")
    print(
        f"  Sesion 1 {first_session.day} {first_session.start_ts.strftime('%H:%M:%S')} -> "
        f"raw={first_session.raw_results} | usadas={first_session.consumed_results} | "
        f"wins={first_session.wins} | ops_usadas={first_session.used_ops}"
    )
    print()

    print("Resumen global:")
    print(f"  Sesiones completas analizadas: {len(sessions)}")
    print(f"  Sesiones ganadas (2 wins antes de 6): {len(sessions) - len(bad_sessions)}")
    print(f"  Sesiones perdidas (<2 wins): {len(bad_sessions)}")
    print(f"  Dias analizados: {total_days}")
    print(f"  Dias que alcanzan $60: {meta_days}")
    print(f"  Dias que NO alcanzan $60: {total_days - meta_days}")
    print(f"  Sesiones ganadas necesarias por dia: {int(META_DIARIA / TARGET_SESSION_WIN)}")
    print()

    print("Primeros 10 dias:")
    for day in sorted(daily.keys(), key=lambda value: datetime.strptime(value, "%d/%m/%Y"))[:10]:
        row = daily[day]
        flag = "SI" if row["meta_hit"] else "NO"
        hit_at = row["meta_hit_at"] or "--:--:--"
        print(
            f"  {day} | sesiones={row['sessions']} | pnl=${row['pnl']:.2f} | "
            f"meta60={flag} | hora_meta={hit_at} | ops_usadas={row['ops_used']}"
        )
    print()

    print("Primeros 15 dias que SI alcanzaron $60:")
    hits = [
        (day, row)
        for day, row in daily.items()
        if row["meta_hit"]
    ]
    for day, row in sorted(hits, key=lambda item: datetime.strptime(item[0], "%d/%m/%Y"))[:15]:
        print(
            f"  {day} | hora_meta={row['meta_hit_at']} | sesiones_totales={row['sessions']} | "
            f"pnl_dia=${row['pnl']:.2f}"
        )
    print()

    print("Dias que NO alcanzaron la meta de $60:")
    misses = [
        (day, row)
        for day, row in daily.items()
        if not row["meta_hit"]
    ]
    for day, row in sorted(misses, key=lambda item: datetime.strptime(item[0], "%d/%m/%Y"))[:20]:
        print(
            f"  {day} | pnl=${row['pnl']:.2f} | sesiones={row['sessions']} | "
            f"sesiones_fallidas={row['losses']}"
        )
    print()

    print("Sesiones mas lentas en llegar a 2 victorias:")
    slowest = sorted(sessions, key=lambda item: (-item.used_ops, item.index))[:10]
    for item in slowest:
        print(
            f"  #{item.index:03d} {item.day} {item.start_ts.strftime('%H:%M:%S')} | "
            f"ops_usadas={item.used_ops} | usadas={item.consumed_results} | raw={item.raw_results}"
        )


if __name__ == "__main__":
    main()