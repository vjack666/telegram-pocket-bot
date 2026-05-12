from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.session_manager import SessionManager


class SessionStatePersistence:
    """Persistencia JSON para SessionManager (session_state.json)."""

    def __init__(self, state_path: str) -> None:
        self._state_path = Path(state_path)

    @property
    def state_path(self) -> Path:
        return self._state_path

    def load_into_session(self, session: SessionManager) -> dict[str, Any]:
        if not self._state_path.exists():
            return {"loaded": False, "reason": "missing_file"}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.warning("[SessionPersist] No se pudo leer estado: %s", exc)
            return {"loaded": False, "reason": "read_error"}

        state = data.get("state", {})
        session.restore_state(state, notify=False)
        return {
            "loaded": True,
            "reason": "ok",
            "saved_at": data.get("saved_at_utc", ""),
        }

    def save_session(self, session: SessionManager, reason: str = "state_change") -> None:
        self.save_snapshot(session.to_dict(), reason=reason)

    def save_snapshot(self, state: dict[str, Any], reason: str = "state_change") -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            now_utc = datetime.now(timezone.utc)
            payload = {
                "saved_at_utc": now_utc.isoformat(),
                "reason": reason,
                "state": state,
            }
            self._state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logging.error("[SessionPersist] No se pudo guardar estado: %s", exc)
