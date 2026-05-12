from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any


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
    - No maneja async ni timing de ejecucion.
    - Solo calcula stake para la siguiente entrada con get_next_stake().
    - Recibe resultado previo ('W' o 'L') y actualiza estado interno.
    - Si la sesion termina en perdida, duplica capital de sesion (Macro-Gale).
    - Si la sesion termina en win, reinicia a capital base de sesion.
    - Puede persistir su estado diario en JSON para sobrevivir reinicios.
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
        state_base_dir: str = "data/masaniello",
        auto_load_state: bool = True,
    ) -> None:
        self._session_base_capital = max(1.0, float(session_base_capital))
        self._initial_session_base_capital = self._session_base_capital
        self._ops_total = max(1, int(ops_total))
        self._wins_needed = max(1, int(wins_needed))
        self._payout = max(0.01, float(payout))
        self._min_stake = max(0.01, float(min_stake))
        self._max_stake = float(max_stake) if max_stake is not None else None
        self._max_gale_multiplier = max(1, int(max_gale_multiplier))
        self._state_base_dir = Path(state_base_dir)
        self._state_base_dir.mkdir(parents=True, exist_ok=True)

        self._gale_multiplier = 1
        self._ops_done = 0
        self._itms = 0
        self._otms = 0
        self._session_over = False

        if auto_load_state:
            self.load_state()

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

    def preview_next_stake(self, last_result: str | None = None) -> float:
        """Calcula el stake siguiente SIN mutar estado interno ni persistencia.

        Útil para UX (prefill de monto) cuando se quiere estimar "si gana/pierde"
        antes de confirmar el resultado real.
        """
        ops_done = self._ops_done
        itms = self._itms
        otms = self._otms
        gale_multiplier = self._gale_multiplier
        session_over = self._session_over

        if last_result is not None:
            r = last_result.strip().upper()
            if r not in {"W", "L"}:
                raise ValueError("last_result debe ser 'W', 'L' o None")

            if session_over:
                session_won = itms >= self._wins_needed
                gale_multiplier = 1 if session_won else min(gale_multiplier * 2, self._max_gale_multiplier)
                ops_done = 0
                itms = 0
                otms = 0
                session_over = False

            ops_done += 1
            if r == "W":
                itms += 1
            else:
                otms += 1

            session_over = self._is_session_over_preview(ops_done, itms)

        if session_over:
            session_won = itms >= self._wins_needed
            gale_multiplier = 1 if session_won else min(gale_multiplier * 2, self._max_gale_multiplier)
            ops_done = 0
            itms = 0
            otms = 0

        return self._compute_stake_preview(ops_done, itms, gale_multiplier)

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
    def session_base_capital(self) -> float:
        return self._session_base_capital

    @property
    def initial_session_base_capital(self) -> float:
        return self._initial_session_base_capital

    def set_session_base_capital(self, new_base: float) -> bool:
        """Actualiza la base de sesion y persiste. Retorna True si cambió."""
        sanitized = max(1.0, round(float(new_base), 2))
        if abs(sanitized - self._session_base_capital) < 1e-9:
            return False
        self._session_base_capital = sanitized
        self.save_state()
        return True

    def save_state(self) -> Path:
        """Guarda el estado actual en un unico JSON diario (sobrescritura)."""
        path = self._daily_state_path()
        payload = {
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
            "state_date": self._today_key(),
            "session_base_capital": self._session_base_capital,
            "initial_session_base_capital": self._initial_session_base_capital,
            "gale_multiplier": self._gale_multiplier,
            "ops_done": self._ops_done,
            "itms": self._itms,
            "otms": self._otms,
            "wins_needed": self._wins_needed,
            "ops_total": self._ops_total,
            "payout": self._payout,
            "session_over": self._session_over,
            "max_gale_multiplier": self._max_gale_multiplier,
        }
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=True, indent=2)
        return path

    def load_state(self) -> bool:
        """Carga el estado diario si existe y coincide con la fecha actual."""
        path = self._daily_state_path()
        if not path.exists():
            return False
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if str(payload.get("state_date", "")) != self._today_key():
                return False
            self._apply_state(payload)
            logging.info("Masaniello: estado diario recuperado desde %s", path)
            return True
        except Exception as exc:
            logging.warning("Masaniello: no se pudo cargar estado diario (%s)", exc)
            return False

    def _today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d")

    def _daily_state_path(self) -> Path:
        return self._state_base_dir / f"masaniello_state_{self._today_key()}.json"

    def _apply_state(self, payload: dict[str, Any]) -> None:
        self._session_base_capital = max(1.0, float(payload.get("session_base_capital", self._session_base_capital)))
        self._initial_session_base_capital = max(
            1.0,
            float(payload.get("initial_session_base_capital", self._initial_session_base_capital)),
        )
        self._gale_multiplier = min(
            max(1, int(payload.get("gale_multiplier", self._gale_multiplier))),
            self._max_gale_multiplier,
        )
        self._ops_done = max(0, int(payload.get("ops_done", self._ops_done)))
        self._itms = max(0, int(payload.get("itms", self._itms)))
        self._otms = max(0, int(payload.get("otms", self._otms)))
        self._session_over = bool(payload.get("session_over", self._session_over))

        # Compatibilidad defensiva por si cambian defaults entre reinicios.
        self._wins_needed = max(1, int(payload.get("wins_needed", self._wins_needed)))
        self._ops_total = max(1, int(payload.get("ops_total", self._ops_total)))

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
        self.save_state()

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

    def _is_session_over_preview(self, ops_done: int, itms: int) -> bool:
        if itms >= self._wins_needed:
            return True
        if ops_done >= self._ops_total:
            return True
        remaining_ops = self._ops_total - ops_done
        remaining_wins_needed = self._wins_needed - itms
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
        self.save_state()

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

    def _compute_stake_preview(self, ops_done: int, itms: int, gale_multiplier: int) -> float:
        restantes = self._ops_total - ops_done
        faltantes = self._wins_needed - itms

        if faltantes <= 0:
            return 0.0
        if faltantes > restantes:
            return 0.0

        p_inv = faltantes / restantes
        denom = 1.0 + self._payout * (1.0 - p_inv)
        if denom <= 0:
            return 0.0

        session_capital = round(self._session_base_capital * gale_multiplier, 2)
        raw = (session_capital * p_inv) / denom
        stake = round(raw, 2)
        stake = max(stake, self._min_stake)
        if self._max_stake is not None:
            stake = min(stake, self._max_stake)
        return round(stake, 2)
