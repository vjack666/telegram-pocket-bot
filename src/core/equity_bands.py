"""EquityBandManager: capital operativo dinámico por tramos de equity.

Separa tres conceptos:
  - Balance real      → dinero actual del broker
  - Base operativa    → referencia de sizing (este módulo la gestiona)
  - Equity protegida  → límite inferior de supervivencia (futuro RiskEngine)

Reglas de cambio de banda:
  - DOWNGRADE: inmediato si el balance cae por debajo del mínimo de la banda actual.
  - UPGRADE  : solo tras N sesiones consecutivas con el balance en la banda superior.

La meta diaria se calcula como porcentaje sobre la base operativa,
no sobre el balance instantáneo ni sobre una base fija.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class EquityBand:
    """Una banda: umbral mínimo de balance → base operativa asignada."""

    min_balance: float
    operational_base: float


def parse_bands(raw: str) -> List[Tuple[float, float]]:
    """Parsea 'min:base,min:base,...' → lista de tuplas ordenadas ascendente.

    Ejemplo: '0:300,400:500,700:800,1200:1500'
    """
    result: List[Tuple[float, float]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 2:
            continue
        try:
            min_b = float(parts[0].strip())
            base = float(parts[1].strip())
        except ValueError:
            continue
        if base > 0:
            result.append((min_b, base))
    return sorted(result, key=lambda x: x[0])


class EquityBandManager:
    """Gestiona la base operativa dinámica por tramos de equity.

    Parameters
    ----------
    bands:
        Lista de (min_balance, operational_base), orden ascendente.
        Ejemplo: [(0, 300), (400, 500), (700, 800), (1200, 1500)]
    upgrade_sessions_required:
        Número de señales consecutivas con balance en banda superior antes de subir.
    daily_target_pct:
        Fracción de base_operativa usada como meta diaria de ganancia.
        Ejemplo: 0.20 → meta = base * 0.20
    initial_balance:
        Balance inicial para fijar la banda de arranque.
    """

    DEFAULT_BANDS: List[Tuple[float, float]] = [
        (0.0, 300.0),
        (400.0, 500.0),
        (700.0, 800.0),
        (1200.0, 1500.0),
    ]

    def __init__(
        self,
        bands: List[Tuple[float, float]],
        upgrade_sessions_required: int = 3,
        daily_target_pct: float = 0.20,
        initial_balance: Optional[float] = None,
        state_path: Optional[str] = None,
        deposit_guard_enabled: bool = False,
        deposit_guard_jump_pct: float = 0.60,
        deposit_guard_cooldown_sessions: int = 3,
    ) -> None:
        if not bands:
            raise ValueError("EquityBandManager: bands no puede estar vacío")

        self._bands: List[EquityBand] = sorted(
            [EquityBand(t, b) for t, b in bands],
            key=lambda x: x.min_balance,
        )
        self._upgrade_sessions_required = max(1, upgrade_sessions_required)
        self._daily_target_pct = max(0.01, daily_target_pct)
        self._state_path = Path(state_path) if state_path else None
        self._deposit_guard_enabled = deposit_guard_enabled
        self._deposit_guard_jump_pct = max(0.05, deposit_guard_jump_pct)
        self._deposit_guard_cooldown_sessions = max(1, deposit_guard_cooldown_sessions)

        start_balance = initial_balance if initial_balance is not None else 0.0
        self._current_base: float = self._band_for_balance(start_balance).operational_base
        self._sessions_in_upper: int = 0
        self._pending_upgrade_to: Optional[float] = None
        self._last_equity: float = start_balance
        self._last_equity_timestamp: str = datetime.now(timezone.utc).isoformat()
        self._upgrade_cooldown_remaining: int = 0

        self._load_state()

        logging.info(
            "[EquityBands] Inicializado: balance=%.2f → base_operativa=%.2f "
            "meta_diaria=%.2f (%.0f%%) upgrade_sesiones=%d",
            start_balance,
            self._current_base,
            self.daily_target,
            self._daily_target_pct * 100,
            self._upgrade_sessions_required,
        )
        self._save_state()

    # ── Propiedades públicas ────────────────────────────────────────────────

    @property
    def operational_base(self) -> float:
        """Base operativa actual usada para sizing."""
        return self._current_base

    @property
    def daily_target(self) -> float:
        """Meta diaria = base_operativa × daily_target_pct."""
        return round(self._current_base * self._daily_target_pct, 2)

    # ── API principal ───────────────────────────────────────────────────────

    def notify_balance(self, balance: float) -> bool:
        """Notifica el balance real del broker y aplica reglas de cambio de banda.

        Retorna True si hubo cambio de banda (upgrade o downgrade).

        Llama a este método:
          - Al arrancar el bot (con el balance inicial)
          - Después de cada resultado de operación (WIN o LOSS)
        """
        target_band = self._band_for_balance(balance)
        target_base = target_band.operational_base
        changed = False
        event_type = self._classify_equity_event(balance)

        if event_type == "deposit_like" and self._deposit_guard_enabled:
            self._upgrade_cooldown_remaining = self._deposit_guard_cooldown_sessions
            logging.warning(
                "[EquityBands] Detectado evento tipo DEPOSITO (equity %.2f → %.2f). "
                "Cooldown upgrade=%d sesiones",
                self._last_equity,
                balance,
                self._upgrade_cooldown_remaining,
            )

        if target_base < self._current_base:
            # ── DOWNGRADE INMEDIATO ─────────────────────────────────────────
            old = self._current_base
            # El umbral relevante es el mínimo de la banda actual (la que se abandona)
            current_band_min = next(
                (b.min_balance for b in reversed(self._bands) if b.operational_base == old),
                target_band.min_balance,
            )
            self._current_base = target_base
            self._sessions_in_upper = 0
            self._pending_upgrade_to = None
            changed = True
            logging.warning(
                "[EquityBands] DOWNGRADE: %.2f → %.2f "
                "(balance=%.2f cayó por debajo de umbral=%.2f)",
                old,
                self._current_base,
                balance,
                current_band_min,
            )

        elif target_base > self._current_base:
            if self._upgrade_cooldown_remaining > 0:
                self._upgrade_cooldown_remaining -= 1
                self._sessions_in_upper = 0
                self._pending_upgrade_to = target_base
                logging.info(
                    "[EquityBands] Upgrade bloqueado por cooldown de deposito "
                    "(restan %d sesiones, balance=%.2f)",
                    self._upgrade_cooldown_remaining,
                    balance,
                )
                self._update_last_equity(balance)
                self._save_state()
                return False

            # ── UPGRADE LENTO ───────────────────────────────────────────────
            if self._pending_upgrade_to != target_base:
                # Nuevo objetivo de upgrade — reiniciar contador
                self._pending_upgrade_to = target_base
                self._sessions_in_upper = 1
            else:
                self._sessions_in_upper += 1

            if self._sessions_in_upper >= self._upgrade_sessions_required:
                old = self._current_base
                self._current_base = target_base
                self._sessions_in_upper = 0
                self._pending_upgrade_to = None
                changed = True
                logging.info(
                    "[EquityBands] UPGRADE: %.2f → %.2f "
                    "(balance=%.2f, tras %d sesiones consecutivas)",
                    old,
                    self._current_base,
                    balance,
                    self._upgrade_sessions_required,
                )
            else:
                logging.info(
                    "[EquityBands] Upgrade pendiente %.2f → %.2f "
                    "(%d/%d sesiones, balance=%.2f)",
                    self._current_base,
                    target_base,
                    self._sessions_in_upper,
                    self._upgrade_sessions_required,
                    balance,
                )
        else:
            # Misma banda — reiniciar contador de upgrade
            self._sessions_in_upper = 0
            self._pending_upgrade_to = None

        self._update_last_equity(balance)
        self._save_state()
        return changed

    def status(self) -> dict:
        """Devuelve el estado actual como dict para logs y telemetría."""
        return {
            "operational_base": self._current_base,
            "daily_target": self.daily_target,
            "daily_target_pct": self._daily_target_pct,
            "sessions_in_upper": self._sessions_in_upper,
            "pending_upgrade_to": self._pending_upgrade_to,
            "upgrade_sessions_required": self._upgrade_sessions_required,
            "upgrade_cooldown_remaining": self._upgrade_cooldown_remaining,
            "last_equity": self._last_equity,
            "last_equity_timestamp": self._last_equity_timestamp,
            "state_path": str(self._state_path) if self._state_path else "",
            "num_bands": len(self._bands),
        }

    # ── Helpers privados ────────────────────────────────────────────────────

    def _band_for_balance(self, balance: float) -> EquityBand:
        """Retorna la banda más alta cuyo min_balance <= balance."""
        result = self._bands[0]
        for band in self._bands:
            if balance >= band.min_balance:
                result = band
            else:
                break
        return result

    def _update_last_equity(self, balance: float) -> None:
        self._last_equity = balance
        self._last_equity_timestamp = datetime.now(timezone.utc).isoformat()

    def _classify_equity_event(self, balance: float) -> str:
        """Clasifica cambios de equity para separar growth operativo de flujos externos."""
        prev = self._last_equity
        if prev <= 0:
            return "unknown"
        delta = balance - prev
        if delta <= 0:
            return "non_positive"

        jump_threshold = max(1.0, self._current_base * self._deposit_guard_jump_pct)
        if delta >= jump_threshold:
            return "deposit_like"
        return "profit_like"

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "operational_base": self._current_base,
                "sessions_in_upper": self._sessions_in_upper,
                "pending_upgrade_to": self._pending_upgrade_to,
                "last_equity": self._last_equity,
                "last_equity_timestamp": self._last_equity_timestamp,
                "upgrade_cooldown_remaining": self._upgrade_cooldown_remaining,
                "daily_target_pct": self._daily_target_pct,
                "upgrade_sessions_required": self._upgrade_sessions_required,
                "bands": [[b.min_balance, b.operational_base] for b in self._bands],
            }
            self._state_path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logging.warning("[EquityBands] No se pudo persistir estado: %s", exc)

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            payload = json.loads(raw)

            persisted_bands = payload.get("bands")
            if persisted_bands:
                current = [[b.min_balance, b.operational_base] for b in self._bands]
                if persisted_bands != current:
                    logging.warning(
                        "[EquityBands] Estado previo ignorado: bandas cambiaron en config"
                    )
                    return

            base = float(payload.get("operational_base", self._current_base))
            self._current_base = max(1.0, base)
            self._sessions_in_upper = int(payload.get("sessions_in_upper", 0))
            pending = payload.get("pending_upgrade_to")
            self._pending_upgrade_to = float(pending) if pending is not None else None
            self._last_equity = float(payload.get("last_equity", self._last_equity))
            self._last_equity_timestamp = str(
                payload.get("last_equity_timestamp", self._last_equity_timestamp)
            )
            self._upgrade_cooldown_remaining = int(
                payload.get("upgrade_cooldown_remaining", 0)
            )
            logging.info(
                "[EquityBands] Estado restaurado desde %s | base=%.2f sesiones=%d "
                "pending=%s cooldown=%d",
                self._state_path,
                self._current_base,
                self._sessions_in_upper,
                self._pending_upgrade_to,
                self._upgrade_cooldown_remaining,
            )
        except Exception as exc:
            logging.warning("[EquityBands] No se pudo restaurar estado persistido: %s", exc)
