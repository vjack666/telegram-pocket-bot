from dataclasses import dataclass

@dataclass(frozen=True)
class RecoveryProfile:
    """Perfil de sizing/recovery configurable, independiente del modo operativo.

    Aplica el cap de exposición a TODOS los pasos (entrada, G1, G2) en
    todos los modos. Los multiplicadores g1_mult / g2_mult son usados por
    el modo 'calculator'; en modo sesión sirven como referencia de gale.

    Regla de cap: siempre min(raw_stake, cap) ANTES de round(), para
    evitar excesos por rounding.
    """
    g1_mult: float             # multiplicador de G1 sobre entrada
    g2_mult: float             # multiplicador de G2 sobre entrada
    max_trade_pct: float       # cap por operación individual (ej: 0.10 = 10%)
    max_total_exposure_pct: float  # cap de exposición acumulada — reservado para RiskEngine futuro
