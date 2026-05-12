from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.pipeline_masaniello import MasanielloSessionState


class MasanielloSessionPersistence:
    """Persistencia en disco para la sesion de Masaniello."""

    def __init__(self, state_path: str) -> None:
        self._state_path = Path(state_path)

    @property
    def state_path(self) -> Path:
        return self._state_path

    def load_into_session(
        self,
        session: MasanielloSessionState,
        reset_if_daily_target_reached: bool = False,
    ) -> dict[str, Any]:
        """Carga estado guardado en la sesion; resetea si es de otro dia o meta diaria ya cumplida."""
        today = datetime.now(timezone.utc).date().isoformat()
        if not self._state_path.exists():
            return {"loaded": False, "reason": "missing_file"}

        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("[MasanielloPersist] No se pudo leer estado: %s", exc)
            return {"loaded": False, "reason": "read_error"}

        saved_date = str(data.get("date_utc", ""))
        if reset_if_daily_target_reached:
            session.reset_session(reason="daily_target_reached_startup", notify=False)
            self.save_session(session, reason="daily_target_reached_startup")
            return {"loaded": False, "reason": "daily_target_reached"}

        if saved_date != today:
            session.reset_daily_counters(notify=False)
            session.reset_session(reason="new_utc_day", notify=False)
            self.save_session(session, reason="new_utc_day")
            return {"loaded": False, "reason": "different_date", "saved_date": saved_date, "today": today}

        state = data.get("state", {})
        session.restore_state(
            wins=state.get("wins", 0),
            losses=state.get("losses", 0),
            session_blocked=state.get("is_session_blocked", False),
            result_history=state.get("result_history", []),
            base_balance=state.get("base_balance"),
            payout_mult=state.get("payout_mult"),
            blocks_won_today=state.get("blocks_won_today", 0),
            blocks_lost_today=state.get("blocks_lost_today", 0),
            global_stop=state.get("global_stop", False),
            notify=False,
        )
        return {
            "loaded": True,
            "reason": "ok",
            "saved_at": data.get("saved_at_utc", ""),
            "state": state,
        }

    def save_session(self, session: MasanielloSessionState, reason: str = "state_change") -> None:
        """Guarda sesion actual a disco (JSON)."""
        self.save_snapshot(session.to_dict(), reason=reason)

    def save_snapshot(self, state: dict[str, Any], reason: str = "state_change") -> None:
        """Guarda snapshot serializable sin depender de objetos vivos de sesion."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            now_utc = datetime.now(timezone.utc)
            payload = {
                "date_utc": now_utc.date().isoformat(),
                "saved_at_utc": now_utc.isoformat(),
                "reason": reason,
                "state": state,
            }
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.error("[MasanielloPersist] No se pudo guardar estado: %s", exc)
