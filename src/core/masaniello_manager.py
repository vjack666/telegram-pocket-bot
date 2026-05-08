from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MasanielloSnapshot:
    session_capital: float
    session_base_capital: float
    gale_multiplier: int
    ops_done: int
    itms: int
    otms: int
    wins_needed: int
    ops_total: int
    is_session_over: bool


class MasanielloManager:
    """Caja negra de sizing Masaniello con Macro-Gale por sesion.

    Reglas:
    - No maneja tiempo, async ni IO.
    - Solo calcula stake para la siguiente entrada con get_next_stake().
    - Recibe resultado previo ('W' o 'L') y actualiza estado interno.
    - Si la sesion termina en perdida, duplica capital de sesion (Macro-Gale).
    - Si la sesion termina en win, reinicia a capital base de sesion.
    """

    def __init__(
        self,
        *,
        session_base_capital: float = 10.0,
        ops_total: int = 6,
        wins_needed: int = 3,
        payout: float = 0.92,
        min_stake: float = 1.0,
        max_stake: float | None = None,
        max_gale_multiplier: int = 16,
    ) -> None:
        self._session_base_capital = max(1.0, float(session_base_capital))
        self._ops_total = max(1, int(ops_total))
        self._wins_needed = max(1, int(wins_needed))
        self._payout = max(0.01, float(payout))
        self._min_stake = max(0.01, float(min_stake))
        self._max_stake = float(max_stake) if max_stake is not None else None
        self._max_gale_multiplier = max(1, int(max_gale_multiplier))

        self._gale_multiplier = 1
        self._ops_done = 0
        self._itms = 0
        self._otms = 0
        self._session_over = False

    def get_next_stake(self, last_result: str | None = None) -> float:
        """Actualiza estado con resultado previo y devuelve stake siguiente.

        last_result:
        - 'W': victoria de ciclo completo (incluye WD/G1/G2)
        - 'L': perdida de ciclo completo
        - None: primer calculo, sin actualizar estado
        """
        if last_result is not None:
            self._consume_result(last_result)

        if self._session_over:
            self._roll_to_next_session()

        return self._compute_stake()

    def snapshot(self) -> MasanielloSnapshot:
        return MasanielloSnapshot(
            session_capital=self._session_capital,
            session_base_capital=self._session_base_capital,
            gale_multiplier=self._gale_multiplier,
            ops_done=self._ops_done,
            itms=self._itms,
            otms=self._otms,
            wins_needed=self._wins_needed,
            ops_total=self._ops_total,
            is_session_over=self._session_over,
        )

    @property
    def _session_capital(self) -> float:
        return round(self._session_base_capital * self._gale_multiplier, 2)

    def _consume_result(self, result: str) -> None:
        r = result.strip().upper()
        if r not in {"W", "L"}:
            raise ValueError("last_result debe ser 'W', 'L' o None")

        if self._session_over:
            self._roll_to_next_session()

        self._ops_done += 1
        if r == "W":
            self._itms += 1
        else:
            self._otms += 1

        self._session_over = self._is_session_over()

    def _is_session_over(self) -> bool:
        if self._itms >= self._wins_needed:
            return True
        if self._ops_done >= self._ops_total:
            return True
        remaining_ops = self._ops_total - self._ops_done
        remaining_wins_needed = self._wins_needed - self._itms
        if remaining_wins_needed > remaining_ops:
            return True
        return False

    def _roll_to_next_session(self) -> None:
        session_won = self._itms >= self._wins_needed
        if session_won:
            self._gale_multiplier = 1
        else:
            self._gale_multiplier = min(
                self._gale_multiplier * 2,
                self._max_gale_multiplier,
            )

        self._ops_done = 0
        self._itms = 0
        self._otms = 0
        self._session_over = False

    def _compute_stake(self) -> float:
        restantes = self._ops_total - self._ops_done
        faltantes = self._wins_needed - self._itms

        if faltantes <= 0:
            return 0.0
        if faltantes > restantes:
            return 0.0

        p_inv = faltantes / restantes
        denom = 1.0 + self._payout * (1.0 - p_inv)
        if denom <= 0:
            return 0.0

        raw = (self._session_capital * p_inv) / denom
        stake = max(self._min_stake, raw)
        if self._max_stake is not None:
            stake = min(stake, self._max_stake)
        return round(stake, 2)
