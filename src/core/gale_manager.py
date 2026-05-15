from __future__ import annotations


class GaleManager:
    def __init__(self, payout: float) -> None:
        self.payout = max(0.01, float(payout))

    def calcular_stakes(self, stake_masaniello: float) -> dict:
        stake_total = max(0.01, float(stake_masaniello))
        gale_factor = 1.0 + (1.0 / self.payout)
        stake_entry = stake_total / (1.0 + gale_factor)
        stake_g1 = stake_total - stake_entry
        return {
            "entry": round(stake_entry, 2),
            "g1": round(stake_g1, 2),
            "total_riesgo": round(stake_total, 2),
        }

    @staticmethod
    def resolver_resultado(result_entry: str, result_g1: str | None = None) -> str:
        entry = str(result_entry).strip().upper()
        g1 = None if result_g1 is None else str(result_g1).strip().upper()

        if entry == "WIN":
            return "WIN"
        if entry == "LOSS" and g1 == "WIN":
            return "WIN"
        if entry == "LOSS" and g1 == "LOSS":
            return "LOSS"
        if entry == "LOSS" and g1 is None:
            return "PENDIENTE"
        raise ValueError(f"Resultados no soportados: entry={result_entry} g1={result_g1}")
