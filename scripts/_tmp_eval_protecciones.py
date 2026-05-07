from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sim_objetivo_60_por_dia import (
    CAPITAL_INICIAL,
    HISTORY,
    N_OPS,
    PAYOUT_MULT,
    W_NEEDED,
    group_sessions,
    masaniello_stake,
    parse_outcomes,
)

BASE = 300.0
WIN_RESULTS = {"WD", "G1", "G2"}


def target_by_balance(b: float, mode: str) -> float:
    if mode == "baseline":
        return 60.0
    if mode == "shield_soft":
        if b < 450:
            return 20.0
        if b < 600:
            return 40.0
        return 60.0
    if mode == "shield_hard":
        if b < 450:
            return 20.0
        if b < 600:
            return 30.0
        return 60.0
    raise ValueError(mode)


def rules_by_balance(b: float, mode: str) -> tuple[float, float | None, int | None]:
    # returns: daily_stop_loss, stake_cap, max_losses_per_session
    if mode == "baseline":
        return 9999.0, None, None
    if mode == "shield_soft":
        if b < 450:
            return 35.0, 40.0, None
        if b < 600:
            return 45.0, None, None
        return 60.0, None, None
    if mode == "shield_hard":
        if b < 450:
            return 30.0, 30.0, 1
        if b < 600:
            return 40.0, 40.0, 1
        return 60.0, None, None
    raise ValueError(mode)


def simulate(mode: str) -> dict:
    outcomes = parse_outcomes(HISTORY)
    sessions = group_sessions(outcomes, N_OPS)

    balance = float(CAPITAL_INICIAL)
    peak = balance
    max_dd = 0.0
    min_balance = balance

    daily = defaultdict(lambda: {
        "start": None,
        "pnl": 0.0,
        "end": None,
        "meta": False,
        "target": 0.0,
        "loss_limit": 0.0,
    })

    for chunk in sessions:
        day = chunk[0].timestamp.strftime("%d/%m/%Y")
        row = daily[day]

        if row["start"] is None:
            row["start"] = round(balance, 2)
            t = target_by_balance(balance, mode)
            stop_loss, _, _ = rules_by_balance(balance, mode)
            row["target"] = t
            row["loss_limit"] = stop_loss

        # stop de dia
        if row["meta"]:
            continue
        if row["pnl"] <= -row["loss_limit"]:
            continue

        stop_loss, stake_cap, max_losses_session = rules_by_balance(row["start"], mode)
        target = row["target"]

        wins = losses = 0
        for outcome in chunk:
            wins_needed = W_NEEDED - wins
            ops_left = N_OPS - (wins + losses)
            if wins_needed <= 0 or wins_needed > ops_left:
                break

            if max_losses_session is not None and losses >= max_losses_session:
                break

            stake = masaniello_stake(BASE, losses, wins, N_OPS, W_NEEDED, PAYOUT_MULT)
            if stake_cap is not None and stake > stake_cap:
                break

            is_win = outcome.result in WIN_RESULTS
            pnl = stake * (PAYOUT_MULT - 1) if is_win else -stake

            balance = round(balance + pnl, 2)
            row["pnl"] = round(row["pnl"] + pnl, 2)
            row["end"] = balance

            if is_win:
                wins += 1
            else:
                losses += 1

            peak = max(peak, balance)
            min_balance = min(min_balance, balance)
            max_dd = max(max_dd, round(peak - balance, 2))

            if row["pnl"] >= target:
                row["meta"] = True
                break
            if row["pnl"] <= -stop_loss:
                break

    # cierre por dias vacios
    for d in daily.values():
        if d["end"] is None:
            d["end"] = d["start"]

    ordered_days = sorted(daily.keys(), key=lambda x: datetime.strptime(x, "%d/%m/%Y"))
    meta_days = sum(1 for d in ordered_days if daily[d]["meta"])
    fail_days = len(ordered_days) - meta_days

    first_10 = ordered_days[:10]

    return {
        "mode": mode,
        "days": len(ordered_days),
        "meta_days": meta_days,
        "fail_days": fail_days,
        "final_balance": balance,
        "profit": round(balance - CAPITAL_INICIAL, 2),
        "max_dd": max_dd,
        "min_balance": min_balance,
        "sample": [
            (d, daily[d]["start"], daily[d]["target"], daily[d]["pnl"], daily[d]["meta"]) for d in first_10
        ],
    }


for mode in ["baseline", "shield_soft", "shield_hard"]:
    r = simulate(mode)
    print("=" * 70)
    print(mode)
    print(f"days={r['days']} | meta={r['meta_days']}/{r['days']} | fail={r['fail_days']}")
    print(f"final=${r['final_balance']:.2f} | profit=${r['profit']:.2f}")
    print(f"max_dd=${r['max_dd']:.2f} | min_balance=${r['min_balance']:.2f}")
    print("first10:")
    for d, st, tgt, pnl, meta in r["sample"]:
        print(f"  {d} start={st:7.2f} target={tgt:5.0f} pnl={pnl:7.2f} meta={meta}")
