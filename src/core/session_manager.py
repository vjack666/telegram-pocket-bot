from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


from src.core.gale_calculator import GaleCalculator



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
    _gale_calc: GaleCalculator = field(init=False, repr=False)

    state_change_callback: Callable[["SessionManager", str], None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        def _env_bool(name: str, default: bool) -> bool:
            raw = os.getenv(name, "").strip().lower()
            if raw == "":
                return default
            return raw in {"1", "true", "yes", "on"}

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

        env_incremento_alto = os.getenv("APP_CALC_INCREMENT", "2").strip()
        env_incremento_bajo = os.getenv("APP_CALC_INCREMENT_BELOW_100", "1").strip()
        env_incremento_umbral = os.getenv("APP_CALC_INCREMENT_THRESHOLD", "100").strip()
        incremento_alto = 2
        incremento_bajo = 1
        incremento_umbral = 100.0
        try:
            incremento_alto = max(1, int(float(env_incremento_alto or "2")))
        except ValueError:
            pass
        try:
            incremento_bajo = max(1, int(float(env_incremento_bajo or "1")))
        except ValueError:
            pass
        try:
            incremento_umbral = float(env_incremento_umbral or "100")
        except ValueError:
            pass
        objetivo_entero_par = _env_bool("APP_CALC_TARGET_EVEN_INTEGER", True)

        # Inicializa el nuevo gale calculator
        self._gale_calc = GaleCalculator(
            saldo_actual=self.capital,
            payout=self.payout,
            incremento=incremento_alto,
            incremento_bajo_umbral=incremento_bajo,
            incremento_umbral=incremento_umbral,
            objetivo_entero_par=objetivo_entero_par,
            objetivo_manual=None,
            usar_multiplicador=False,
            multiplicador=2.0,
        )
        self._wins: int = 0
        self.capital_inicial_sesion = round(self.capital, 2)
        self.balance_actual = self.capital
        self._touch()

    @property
    def signals_consumed(self) -> int:
        return self._wins + self._gale_calc.perdidas

    @property
    def wins(self) -> int:
        return self._wins

    @property
    def losses(self) -> int:
        return self._gale_calc.perdidas

    @property
    def n_ops(self) -> int:
        return self.n

    @property
    def w_needed(self) -> int:
        return self.k

    @property
    def base_balance(self) -> float:
        return self.capital_inicial_sesion

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
        self._gale_calc.saldo_actual = self.balance_actual

    def sync_accumulated_loss(self, amount: float) -> None:
        # Legacy no-op para mantener compatibilidad con llamadas existentes.
        _ = amount

    def get_stakes_para_senal(self, min_order: float = 0.01) -> dict:
        # Calcula el stake usando la lógica avanzada de gale/martingala
        self._gale_calc.saldo_actual = self.balance_actual
        self._gale_calc.payout = self.payout
        self._gale_calc.recalcular_inversion()
        entry = max(min_order, round(self._gale_calc.inversion_actual, 2))
        g1 = self.peek_next_stake_if_loss(min_order=min_order)
        max_riesgo = round(entry + g1, 2)
        self._last_stakes = {"entry": entry, "g1": g1, "max_riesgo": max_riesgo}
        self._touch()
        self._notify("gale_stake_calculado")
        return self._last_stakes.copy()

    def get_next_stake(self, min_order: float = 0.01) -> float:
        return self.get_stakes_para_senal(min_order=min_order)["entry"]

    def current_entry_stake(self) -> float:
        return self.get_next_stake()

    def _clone_gale_calc(self) -> GaleCalculator:
        """Clona el estado interno del calculador para simulaciones sin side effects."""
        return GaleCalculator(
            saldo_actual=float(self._gale_calc.saldo_actual),
            payout=float(self._gale_calc.payout),
            incremento=int(self._gale_calc.incremento),
            incremento_bajo_umbral=int(self._gale_calc.incremento_bajo_umbral),
            incremento_umbral=float(self._gale_calc.incremento_umbral),
            objetivo_entero_par=bool(self._gale_calc.objetivo_entero_par),
            objetivo_manual=self._gale_calc.objetivo_manual,
            usar_multiplicador=bool(self._gale_calc.usar_multiplicador),
            multiplicador=float(self._gale_calc.multiplicador),
            perdidas=int(self._gale_calc.perdidas),
            inversion_base=float(self._gale_calc.inversion_base),
            inversion_actual=float(self._gale_calc.inversion_actual),
            saldo_objetivo=float(self._gale_calc.saldo_objetivo),
            regla10_limite=float(self._gale_calc.regla10_limite),
            regla10_tolerancia_pct=float(self._gale_calc.regla10_tolerancia_pct),
            mensaje=str(self._gale_calc.mensaje),
        )

    def peek_stake_after_losses(self, losses_ahead: int, min_order: float = 0.01) -> float:
        """Simula el stake futuro tras N pérdidas consecutivas desde el estado actual."""
        simulated = self._clone_gale_calc()
        for _ in range(max(0, int(losses_ahead))):
            simulated.on_perdio()
        return max(min_order, round(simulated.inversion_actual, 2))

    def peek_next_stake_if_loss(self, min_order: float = 0.01) -> float:
        return self.peek_stake_after_losses(losses_ahead=1, min_order=min_order)

    def registrar_resultado_senal(self, result_entry: str, result_g1: str | None = None) -> dict:
        if self.sesion_pausada:
            return {
                "registrado": False,
                "resultado_final": "PAUSADA",
                "sesion_pausada": True,
            }

        if result_entry.upper() in {"WIN", "W"}:
            self._gale_calc.on_gano()
            self._wins += 1
            resultado_final = "WIN"
        elif result_entry.upper() in {"LOSS", "L"}:
            self._gale_calc.on_perdio()
            resultado_final = "LOSS"
        else:
            resultado_final = "PENDIENTE"

        self.last_result_label = resultado_final
        self.last_closed_at_utc = datetime.now(timezone.utc).isoformat()
        self.balance_actual = self._gale_calc.saldo_actual

        if resultado_final == "LOSS":
            self.blocks_lost_today += 1

        self._touch()
        self._evaluar_stop_loss_sesion()
        self._notify(f"gale_resultado:{resultado_final}")

        return {
            "registrado": True,
            "resultado_final": resultado_final,
            "reinicio_ciclo": False,
            "sesion_pausada": self.sesion_pausada,
            "estado": self.get_estado(),
        }

    def _aplicar_loss_externo_con_reglas(self) -> None:
        """Aplica transición LOSS respetando reglas del gale usando saldo real ya reconciliado.

        Esta ruta se usa para operaciones manuales detectadas por balance real del broker.
        """
        self._gale_calc.perdidas += 1
        saldo = max(0.0, float(self._gale_calc.saldo_actual))
        limite = int(saldo * 0.10)

        if not self._gale_calc.regla10_activa() and self._gale_calc.perdidas >= 3:
            self._gale_calc.perdidas = 0
            self._gale_calc.recalcular_inversion()
            self._gale_calc.mensaje = "🔄 Reset gale por 3 pérdidas (saldo <= $50)"
            return

        if self._gale_calc.usar_multiplicador:
            siguiente = self._gale_calc.inversion_actual * self._gale_calc.multiplicador
        else:
            utilidad_necesaria = self._gale_calc.saldo_objetivo - saldo
            siguiente = (
                utilidad_necesaria / self._gale_calc.payout
                if self._gale_calc.payout > 0 and utilidad_necesaria > 0
                else 0.0
            )

        if self._gale_calc.regla10_activa() and siguiente >= limite:
            self._gale_calc.perdidas = 0
            self._gale_calc.recalcular_inversion()
            self._gale_calc.mensaje = "⚠️ Reset por riesgo (>10% de la cuenta)"
            return

        self._gale_calc.inversion_actual = siguiente
        self._gale_calc.mensaje = f"❌ Perdiste - Gale {self._gale_calc.perdidas}"

    def registrar_resultado_externo(
        self,
        result_label: str,
        balance_before: float,
        balance_after: float,
    ) -> dict:
        """Reconciliación de resultado externo (manual) con saldo real del broker.

        Conserva reglas de la calculadora y evita doble descuento de saldo.
        """
        if self.sesion_pausada:
            return {
                "registrado": False,
                "resultado_final": "PAUSADA",
                "sesion_pausada": True,
            }

        normalized = str(result_label).strip().upper()
        self.balance_actual = max(0.0, float(balance_after))
        self._gale_calc.saldo_actual = self.balance_actual

        if normalized in {"WIN", "W", "WIN DIRECTO", "G1"}:
            self._wins += 1
            self._gale_calc.perdidas = 0
            self._gale_calc.objetivo_manual = None
            self._gale_calc.recalcular_inversion()
            self._gale_calc.mensaje = (
                f"✅ Ganaste (manual) | saldo reconciliado: ${self.balance_actual:.2f}"
            )
            resultado_final = "WIN"
        elif normalized in {"LOSS", "L"}:
            self._aplicar_loss_externo_con_reglas()
            resultado_final = "LOSS"
            self.blocks_lost_today += 1
        else:
            raise ValueError(f"Resultado externo no soportado: {result_label}")

        self.last_result_label = resultado_final
        self.last_closed_at_utc = datetime.now(timezone.utc).isoformat()
        self._touch()
        self._evaluar_stop_loss_sesion()
        self._notify(f"gale_resultado_externo:{resultado_final}")

        return {
            "registrado": True,
            "resultado_final": resultado_final,
            "reinicio_ciclo": False,
            "sesion_pausada": self.sesion_pausada,
            "estado": self.get_estado(),
            "balance_before": float(balance_before),
            "balance_after": self.balance_actual,
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
        if self.balance_actual < floor:
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
        self.capital_inicial_sesion = round(capital, 2)
        self.balance_actual = round(capital, 2)
        self._gale_calc.saldo_actual = round(capital, 2)
        self._touch()

    def update_payout_mult(self, payout_mult: float) -> None:
        payout = max(0.01, float(payout_mult) - 1.0)
        self.update_payout(payout)

    def update_payout(self, payout: float) -> None:
        self.payout = max(0.01, float(payout))
        self._gale_calc.payout = self.payout
        self._touch()

    def get_estado(self) -> dict:
        gale_state = self._gale_calc.get_estado()
        return {
            "op_actual": self.signals_consumed + 1,
            "wins": self._wins,
            "losses": self._gale_calc.perdidas,
            "capital_ciclo": self.balance_actual,
            "capital_inicial": self.capital_inicial_sesion,
            "n": self.n,
            "k": self.k,
            "payout": self.payout,
            "reinversion_pct": self.reinversion_pct,
            "sesion_pausada": self.sesion_pausada,
            "capital_inicial_sesion": self.capital_inicial_sesion,
            "stop_loss_pct": self.stop_loss_pct,
            "last_result_label": self.last_result_label,
            "last_stakes": self._last_stakes.copy(),
            "inversion_actual": gale_state["inversion_actual"],
            "gale_mensaje": gale_state["mensaje"],
        }

    def to_dict(self) -> dict:
        return {
            "gale_state": self._gale_calc.get_estado(),
            "wins": self._wins,
            "losses": self._gale_calc.perdidas,
            "payout": self.payout,
            "capital": self.capital,
            "balance_actual": self.balance_actual,
            "sesion_pausada": self.sesion_pausada,
            "capital_inicial_sesion": self.capital_inicial_sesion,
            "last_result_label": self.last_result_label,
            "timestamp_ultima_actualizacion": self._last_updated_at,
        }

    def restore_state(self, state: dict, notify: bool = False) -> None:
        payload = state.get("state", state)
        if not isinstance(payload, dict):
            return

        self.payout = max(0.01, float(payload.get("payout", self.payout)))
        self.balance_actual = float(payload.get("balance_actual", self.capital))
        self.capital_inicial_sesion = float(payload.get("capital_inicial_sesion", self.capital))
        self.sesion_pausada = bool(payload.get("sesion_pausada", False))
        self.last_result_label = str(payload.get("last_result_label", ""))
        self._last_updated_at = str(payload.get("timestamp_ultima_actualizacion", ""))
        self._wins = int(payload.get("wins", 0))
        self._gale_calc.saldo_actual = self.balance_actual
        self._gale_calc.payout = self.payout
        self._gale_calc.perdidas = int(payload.get("losses", 0))
        self._gale_calc.recalcular_inversion()

        self._evaluar_stop_loss_sesion()
        if notify:
            self._notify("restore_state")
