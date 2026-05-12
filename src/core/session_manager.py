from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable


@dataclass
class SessionManager:
    """Gestion por objetivos de sesion (6 mensajes, objetivo neto $10)."""

    max_messages_per_session: int = 6
    target_profit_session: float = 10.0
    target_profit_per_win: float = 5.0
    stop_loss_count: int = 3
    payout: float = 0.92

    messages_in_session: int = 0
    wins: int = 0
    losses: int = 0
    accumulated_loss: float = 0.0
    sessions_closed: int = 0
    sessions_won: int = 0
    sessions_lost: int = 0
    last_close_reason: str = ""
    last_closed_at_utc: str = ""
    last_result_label: str = ""

    state_change_callback: Callable[["SessionManager", str], None] | None = field(default=None, repr=False)

    # Compatibilidad minima con el engine actual
    session_blocked: bool = False
    global_stop: bool = False
    blocks_lost_today: int = 0
    n_ops: int = 6
    w_needed: int = 2
    base_balance: float = 10.0

    def __post_init__(self) -> None:
        self.n_ops = self.max_messages_per_session
        self.w_needed = int(self.target_profit_session / self.target_profit_per_win)

    @property
    def signals_consumed(self) -> int:
        return self.messages_in_session

    def set_state_change_callback(self, callback: Callable[["SessionManager", str], None]) -> None:
        self.state_change_callback = callback

    def _notify(self, reason: str) -> None:
        if self.state_change_callback is None:
            return
        self.state_change_callback(self, reason)

    def sync_accumulated_loss(self, amount: float) -> None:
        self.accumulated_loss = max(0.0, float(amount))
        self._notify("sync_accumulated_loss")

    def get_next_stake(self, min_order: float = 0.01) -> float:
        """API principal de stake para el motor automatico."""
        stake = (self.accumulated_loss + self.target_profit_per_win) / max(0.01, self.payout)
        return round(max(float(min_order), stake), 2)

    def current_entry_stake(self) -> float:
        """Compatibilidad retroactiva; usar get_next_stake()."""
        return self.get_next_stake()

    def peek_next_stake_if_loss(self) -> float:
        # Proyeccion conservadora: si pierde la entrada siguiente, suma ese stake a la deuda.
        projected_loss = self.accumulated_loss + self.current_entry_stake()
        stake = (projected_loss + self.target_profit_per_win) / max(0.01, self.payout)
        return round(max(0.01, stake), 2)

    def update_session_status(self, result_label: str, debt_after_loss: float | None = None) -> dict:
        """API principal para sincronizar resultado de una señal cerrada.

        result_label esperado:
        - WIN DIRECTO / G1 / G2 / WIN
        - LOSS
        """
        if self.global_stop:
            return {
                "session_closed": False,
                "close_reason": self.last_close_reason,
                "stop_triggered": True,
                "global_stop": True,
            }

        normalized = str(result_label).strip().upper()
        is_win = normalized in {"WIN DIRECTO", "G1", "G2", "WIN", "W"}
        is_loss = normalized in {"LOSS", "L"}
        if not is_win and not is_loss:
            raise ValueError(f"Resultado de sesión no soportado: {result_label}")

        self.messages_in_session += 1
        self.last_result_label = normalized

        if is_win:
            self.wins += 1
            self.accumulated_loss = 0.0
        else:
            self.losses += 1
            if debt_after_loss is not None:
                self.accumulated_loss = max(0.0, float(debt_after_loss))

        self._notify(f"update_session_status:{normalized}")
        close_reason = self._close_if_needed()
        return {
            "session_closed": bool(close_reason),
            "close_reason": close_reason,
            "stop_triggered": close_reason == "stop_loss_3_losses",
            "global_stop": self.global_stop,
            "wins": self.wins,
            "losses": self.losses,
            "messages_in_session": self.messages_in_session,
        }

    def record_win(self) -> None:
        self.update_session_status("WIN")

    def record_loss(self, amount: float | None = None) -> None:
        self.update_session_status("LOSS", debt_after_loss=amount)

    def _close_if_needed(self) -> str:
        target_wins = int(self.target_profit_session / self.target_profit_per_win)
        reason = ""
        if self.wins >= target_wins:
            self.sessions_won += 1
            reason = "take_profit_2_wins"
        elif self.losses >= self.stop_loss_count:
            self.sessions_lost += 1
            self.blocks_lost_today += 1
            reason = "stop_loss_3_losses"
        elif self.messages_in_session >= self.max_messages_per_session:
            reason = "max_6_messages"

        if not reason:
            return ""

        self.sessions_closed += 1
        self.last_close_reason = reason
        self.last_closed_at_utc = datetime.now(timezone.utc).isoformat()

        if reason == "stop_loss_3_losses":
            self.global_stop = True
            self.session_blocked = True
            self.reset_session(reason=reason, notify=False, clear_stop_flags=False)
        else:
            self.reset_session(reason=reason, notify=False, clear_stop_flags=True)

        self._notify(f"session_closed:{reason}")
        return reason

    def reset_session(
        self,
        reason: str = "manual_reset",
        notify: bool = True,
        clear_stop_flags: bool = True,
    ) -> None:
        self.messages_in_session = 0
        self.wins = 0
        self.losses = 0
        self.accumulated_loss = 0.0
        self.last_result_label = ""
        if clear_stop_flags:
            self.session_blocked = False
            self.global_stop = False
        if notify:
            self._notify(f"reset:{reason}")

    def reset_daily_counters(self, notify: bool = True) -> None:
        self.blocks_lost_today = 0
        self.global_stop = False
        self.session_blocked = False
        if notify:
            self._notify("reset:daily_counters")

    def update_base(self, new_base: float) -> None:
        self.base_balance = max(1.0, float(new_base))

    def update_payout_mult(self, payout_mult: float) -> None:
        self.payout = max(0.01, float(payout_mult) - 1.0)

    def to_dict(self) -> dict:
        return {
            "max_messages_per_session": self.max_messages_per_session,
            "target_profit_session": self.target_profit_session,
            "target_profit_per_win": self.target_profit_per_win,
            "stop_loss_count": self.stop_loss_count,
            "payout": self.payout,
            "messages_in_session": self.messages_in_session,
            "wins": self.wins,
            "losses": self.losses,
            "accumulated_loss": self.accumulated_loss,
            "sessions_closed": self.sessions_closed,
            "sessions_won": self.sessions_won,
            "sessions_lost": self.sessions_lost,
            "last_close_reason": self.last_close_reason,
            "last_closed_at_utc": self.last_closed_at_utc,
            "last_result_label": self.last_result_label,
            "is_session_blocked": self.session_blocked,
            "global_stop": self.global_stop,
            "blocks_lost_today": self.blocks_lost_today,
        }

    def restore_state(self, state: dict, notify: bool = False) -> None:
        self.messages_in_session = int(state.get("messages_in_session", 0))
        self.wins = int(state.get("wins", 0))
        self.losses = int(state.get("losses", 0))
        self.accumulated_loss = float(state.get("accumulated_loss", 0.0))
        self.sessions_closed = int(state.get("sessions_closed", 0))
        self.sessions_won = int(state.get("sessions_won", 0))
        self.sessions_lost = int(state.get("sessions_lost", 0))
        self.last_close_reason = str(state.get("last_close_reason", ""))
        self.last_closed_at_utc = str(state.get("last_closed_at_utc", ""))
        self.last_result_label = str(state.get("last_result_label", ""))
        self.session_blocked = bool(state.get("is_session_blocked", False))
        self.global_stop = bool(state.get("global_stop", False))
        self.blocks_lost_today = int(state.get("blocks_lost_today", 0))
        if notify:
            self._notify("restore_state")
