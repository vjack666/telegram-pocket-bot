from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from src.core.gale_manager import GaleManager
from src.core.masaniello_engine import MasanielloEngine


@dataclass
class SessionManager:
    """Wrapper de money management: Masaniello (señal) + G1 interno."""

    capital: float = 100.0
    n: int = 10
    k: int = 7
    payout: float = 0.92
    reinversion_pct: float = 1.0
    max_stake_pct_of_capital: float = 0.05
    stop_loss_pct: float = 0.20

    sesion_pausada: bool = False
    capital_inicial_sesion: float = 100.0
    last_result_label: str = ""
    last_closed_at_utc: str = ""
    blocks_lost_today: int = 0
    balance_actual: float = 0.0

    _last_stakes: dict = field(default_factory=dict, repr=False)
    _last_updated_at: str = field(default="", repr=False)
    _masaniello: MasanielloEngine = field(init=False, repr=False)
    _gale: GaleManager = field(init=False, repr=False)

    state_change_callback: Callable[["SessionManager", str], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.capital = max(0.0, float(self.capital))
        self.payout = max(0.01, float(self.payout))
        env_max_stake_pct = os.getenv("MASANIELLO_MAX_STAKE_PCT", "").strip()
        if env_max_stake_pct:
            try:
                self.max_stake_pct_of_capital = float(env_max_stake_pct)
            except ValueError:
                pass
        self.max_stake_pct_of_capital = max(0.0, min(1.0, float(self.max_stake_pct_of_capital)))
        self.stop_loss_pct = max(0.0, min(0.95, float(self.stop_loss_pct)))

        self._masaniello = MasanielloEngine(
            capital=self.capital,
            n=self.n,
            k=self.k,
            payout=self.payout,
            reinversion_pct=self.reinversion_pct,
        )
        self._gale = GaleManager(self.payout)

        self.capital_inicial_sesion = round(self._masaniello.capital_ciclo, 2)
        self.balance_actual = self._masaniello.capital_ciclo
        self._touch()

    @property
    def signals_consumed(self) -> int:
        return max(0, self._masaniello.op_actual - 1)

    @property
    def wins(self) -> int:
        return self._masaniello.wins

    @property
    def losses(self) -> int:
        return self._masaniello.losses

    @property
    def n_ops(self) -> int:
        return self._masaniello.n

    @property
    def w_needed(self) -> int:
        return self._masaniello.k

    @property
    def base_balance(self) -> float:
        return self._masaniello.capital_inicial

    @property
    def session_blocked(self) -> bool:
        return self.sesion_pausada

    @property
    def global_stop(self) -> bool:
        return self.sesion_pausada

    def set_state_change_callback(self, callback: Callable[["SessionManager", str], None]) -> None:
        self.state_change_callback = callback

    def _notify(self, reason: str) -> None:
        if self.state_change_callback is None:
            return
        self.state_change_callback(self, reason)

    def _touch(self) -> None:
        self._last_updated_at = datetime.now(timezone.utc).isoformat()

    def observe_balance(self, current_balance: float) -> None:
        self.balance_actual = max(0.0, float(current_balance))

    def sync_accumulated_loss(self, amount: float) -> None:
        # Legacy no-op para mantener compatibilidad con llamadas existentes.
        _ = amount

    def get_stakes_para_senal(self, min_order: float = 0.01) -> dict:
        # Stake fijo de $1 independientemente de Masaniello o gestión dinámica.
        entry = 1.0
        g1 = 1.0
        max_riesgo = round(entry + g1, 2)
        self._last_stakes = {"entry": entry, "g1": g1, "max_riesgo": max_riesgo}
        self._touch()
        self._notify("masaniello_stake_calculado")
        return self._last_stakes.copy()

    def get_next_stake(self, min_order: float = 0.01) -> float:
        return self.get_stakes_para_senal(min_order=min_order)["entry"]

    def current_entry_stake(self) -> float:
        return self.get_next_stake()

    def peek_next_stake_if_loss(self, min_order: float = 0.01) -> float:
        # Stake fijo de $1.
        return 1.0

    def registrar_resultado_senal(self, result_entry: str, result_g1: str | None = None) -> dict:
        if self.sesion_pausada:
            return {
                "registrado": False,
                "resultado_final": "PAUSADA",
                "sesion_pausada": True,
            }

        resultado_final = self._gale.resolver_resultado(result_entry, result_g1)
        if resultado_final == "PENDIENTE":
            return {
                "registrado": False,
                "resultado_final": resultado_final,
                "sesion_pausada": self.sesion_pausada,
            }

        op_antes = self._masaniello.op_actual
        self._masaniello.registrar_resultado(resultado_final)
        reinicio_ciclo = op_antes > self._masaniello.op_actual

        self.last_result_label = resultado_final
        self.last_closed_at_utc = datetime.now(timezone.utc).isoformat()
        self.balance_actual = self._masaniello.capital_ciclo

        if resultado_final == "LOSS":
            self.blocks_lost_today += 1

        self._touch()
        self._evaluar_stop_loss_sesion()
        self._notify(f"masaniello_resultado:{resultado_final}")

        return {
            "registrado": True,
            "resultado_final": resultado_final,
            "reinicio_ciclo": reinicio_ciclo,
            "sesion_pausada": self.sesion_pausada,
            "estado": self.get_estado(),
        }

    def update_session_status(
        self,
        result_label: str,
        debt_after_loss: float | None = None,
        current_balance: float | None = None,
    ) -> dict:
        _ = debt_after_loss
        if current_balance is not None:
            self.observe_balance(current_balance)

        normalized = str(result_label).strip().upper()
        if normalized in {"WIN DIRECTO", "G1", "WIN", "W"}:
            return self.registrar_resultado_senal("WIN", None)
        if normalized in {"LOSS", "L"}:
            return self.registrar_resultado_senal("LOSS", "LOSS")
        raise ValueError(f"Resultado de sesión no soportado: {result_label}")

    def record_win(self) -> None:
        self.registrar_resultado_senal("WIN", None)

    def record_loss(self, amount: float | None = None) -> None:
        _ = amount
        self.registrar_resultado_senal("LOSS", "LOSS")

    def _evaluar_stop_loss_sesion(self) -> None:
        floor = self.capital_inicial_sesion * (1.0 - self.stop_loss_pct)
        if self._masaniello.capital_ciclo < floor:
            self.sesion_pausada = True

    def reset_session(
        self,
        reason: str = "manual_reset",
        notify: bool = True,
        clear_stop_flags: bool = True,
    ) -> None:
        self.last_result_label = ""
        if clear_stop_flags:
            self.sesion_pausada = False
        self._touch()
        if notify:
            self._notify(f"reset:{reason}")

    def reset_daily_counters(self, notify: bool = True) -> None:
        self.blocks_lost_today = 0
        self.sesion_pausada = False
        self._touch()
        if notify:
            self._notify("reset:daily_counters")

    def update_base(self, new_base: float) -> None:
        capital = max(0.0, float(new_base))
        self._masaniello.capital = capital
        self._masaniello.capital_inicial = round(capital, 2)
        self._masaniello.capital_ciclo = round(capital, 2)
        self.capital_inicial_sesion = round(capital, 2)
        self._touch()

    def update_payout_mult(self, payout_mult: float) -> None:
        payout = max(0.01, float(payout_mult) - 1.0)
        self.update_payout(payout)

    def update_payout(self, payout: float) -> None:
        self.payout = max(0.01, float(payout))
        self._masaniello.payout = self.payout
        self._gale.payout = self.payout
        self._touch()

    def get_estado(self) -> dict:
        masaniello_state = self._masaniello.to_dict()
        return {
            "op_actual": masaniello_state["op_actual"],
            "wins": masaniello_state["wins"],
            "losses": masaniello_state["losses"],
            "capital_ciclo": masaniello_state["capital_ciclo"],
            "capital_inicial": masaniello_state["capital_inicial"],
            "n": masaniello_state["n"],
            "k": masaniello_state["k"],
            "payout": masaniello_state["payout"],
            "reinversion_pct": masaniello_state["reinversion_pct"],
            "sesion_pausada": self.sesion_pausada,
            "capital_inicial_sesion": self.capital_inicial_sesion,
            "stop_loss_pct": self.stop_loss_pct,
            "last_result_label": self.last_result_label,
            "last_stakes": self._last_stakes.copy(),
        }

    def to_dict(self) -> dict:
        return {
            "masaniello": self._masaniello.to_dict(),
            "sesion_pausada": self.sesion_pausada,
            "capital_inicial_sesion": self.capital_inicial_sesion,
            "timestamp_ultima_actualizacion": self._last_updated_at,
        }

    def restore_state(self, state: dict, notify: bool = False) -> None:
        payload = state.get("state", state)
        masaniello_payload = payload.get("masaniello") if isinstance(payload, dict) else None

        if isinstance(masaniello_payload, dict):
            self._masaniello = MasanielloEngine.from_dict(masaniello_payload)
            self._gale = GaleManager(float(masaniello_payload.get("payout", self.payout)))
            self.payout = self._gale.payout
            self.sesion_pausada = bool(payload.get("sesion_pausada", False))
            self.capital_inicial_sesion = float(
                payload.get("capital_inicial_sesion", self._masaniello.capital_inicial)
            )
            self._last_updated_at = str(payload.get("timestamp_ultima_actualizacion", ""))
            self.balance_actual = self._masaniello.capital_ciclo
        else:
            # Fallback compatible para snapshots legacy.
            self.payout = max(0.01, float(payload.get("payout", self.payout)))
            self._masaniello = MasanielloEngine(
                capital=float(payload.get("balance_actual", self.capital)),
                n=int(payload.get("max_messages_per_session", self.n)),
                k=max(1, int(payload.get("wins", 0)) + 1),
                payout=self.payout,
                reinversion_pct=self.reinversion_pct,
            )
            self._gale = GaleManager(self.payout)
            self.sesion_pausada = bool(payload.get("global_stop", False) or payload.get("is_session_blocked", False))
            self.capital_inicial_sesion = float(payload.get("balance_maximo_historico", self._masaniello.capital_inicial))
            self.balance_actual = float(payload.get("balance_actual", self._masaniello.capital_ciclo))
            self._last_updated_at = datetime.now(timezone.utc).isoformat()

        self._evaluar_stop_loss_sesion()
        if notify:
            self._notify("restore_state")
