"""Daily Profit Tracker: Trackea metas diarias y cambia riesgo cuando se alcanza."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional


class DailyProfitTracker:
    """
    Trackea ganancias/pérdidas diarias y controla riesgo.

    Características:
    - Meta diaria dinámica (base × daily_target_pct)
    - Trackea PnL real del día
    - Detecta cuando se alcanza meta
    - Cambia a modo "defensivo" después de meta
    - Se reinicia automáticamente a medianoche UTC

    Modos:
    - AGGRESSIVE: Normal, busca la meta
    - DEFENSIVE: Después de alcanzar meta, reduce riesgo
    """

    def __init__(
        self,
        daily_target: float = 60.0,
        state_path: Optional[str] = None,
        enable_defensive_mode: bool = True,
    ):
        """
        Args:
            daily_target: Meta diaria en dinero ($60)
            state_path: Donde guardar estado (JSON)
            enable_defensive_mode: Si True, cambia a defensive después de meta
        """
        self._daily_target = max(0.01, daily_target)
        self._state_path = Path(state_path) if state_path else None
        self._enable_defensive_mode = enable_defensive_mode

        # Estado del día actual
        self._today: date = date.today()
        self._daily_pnl: float = 0.0  # Ganancia/pérdida acumulada hoy
        self._meta_reached: bool = False  # Se alcanzó la meta hoy
        self._defensive_mode: bool = False  # Actualmente en defensive
        self._trades_today: int = 0  # Cantidad de trades hoy
        self._mode_start_time: str = datetime.now(timezone.utc).isoformat()

        self._load_state()

        logging.info(
            "[DailyProfitTracker] Inicializado: meta_diaria=%.2f defensive_enabled=%s",
            self._daily_target,
            self._enable_defensive_mode,
        )

    # ── Propiedades ─────────────────────────────────────────────────────────

    @property
    def daily_target(self) -> float:
        """Meta diaria en dinero."""
        return self._daily_target

    @property
    def daily_pnl(self) -> float:
        """PnL acumulado hoy."""
        return self._daily_pnl

    @property
    def meta_reached(self) -> bool:
        """¿Se alcanzó la meta diaria?"""
        return self._meta_reached

    @property
    def defensive_mode(self) -> bool:
        """¿Está en modo defensivo?"""
        return self._defensive_mode

    @property
    def trades_today(self) -> int:
        """Cantidad de trades ejecutados hoy."""
        return self._trades_today

    @property
    def progress_pct(self) -> float:
        """Progreso hacia la meta (0-100)."""
        if self._daily_target <= 0:
            return 0.0
        return min(100.0, (self._daily_pnl / self._daily_target) * 100)

    # ── API principal ───────────────────────────────────────────────────────

    def record_trade(self, pnl: float) -> dict:
        """
        Registra un trade y actualiza PnL diario.

        Args:
            pnl: Ganancia/pérdida de este trade

        Returns:
            Dict con estado actualizado y recomendaciones
        """
        self._check_day_reset()

        self._daily_pnl = round(self._daily_pnl + pnl, 2)
        self._trades_today += 1

        # Detectar si alcanzó la meta
        was_meta_reached = self._meta_reached
        self._meta_reached = self._daily_pnl >= self._daily_target

        # Actualizar modo defensivo
        should_be_defensive = self._meta_reached and self._enable_defensive_mode
        if should_be_defensive and not self._defensive_mode:
            self._defensive_mode = True
            self._mode_start_time = datetime.now(timezone.utc).isoformat()
            logging.info(
                "[DailyProfitTracker] ✓ META ALCANZADA en trade #%d: $%.2f (meta: $%.2f) → DEFENSIVE MODE ACTIVADO",
                self._trades_today,
                self._daily_pnl,
                self._daily_target,
            )

        self._save_state()

        return {
            "daily_pnl": self._daily_pnl,
            "daily_target": self._daily_target,
            "progress_pct": self.progress_pct,
            "meta_reached": self._meta_reached,
            "meta_just_reached": self._meta_reached and not was_meta_reached,
            "defensive_mode": self._defensive_mode,
            "trades_today": self._trades_today,
            "recommendation": self._get_recommendation(),
        }

    def update_target(self, new_target: float) -> None:
        """Actualiza la meta diaria (p.ej., cuando cambia la base operativa)."""
        self._daily_target = max(0.01, new_target)
        logging.info("[DailyProfitTracker] Meta diaria actualizada: $%.2f", self._daily_target)
        self._save_state()

    def reset_daily(self) -> None:
        """Reinicia el contador diario (típicamente llamado a medianoche)."""
        logging.info(
            "[DailyProfitTracker] Reiniciando día: PnL final = $%.2f, Trades = %d",
            self._daily_pnl,
            self._trades_today,
        )
        self._today = date.today()
        self._daily_pnl = 0.0
        self._meta_reached = False
        self._defensive_mode = False
        self._trades_today = 0
        self._mode_start_time = datetime.now(timezone.utc).isoformat()
        self._save_state()

    def status(self) -> dict:
        """Retorna estado completo para logging/monitoreo."""
        return {
            "date": self._today.isoformat(),
            "daily_pnl": self._daily_pnl,
            "daily_target": self._daily_target,
            "progress_pct": self.progress_pct,
            "meta_reached": self._meta_reached,
            "defensive_mode": self._defensive_mode,
            "trades_today": self._trades_today,
            "mode_since": self._mode_start_time,
        }

    # ── Persistencia ────────────────────────────────────────────────────────

    def _check_day_reset(self) -> None:
        """Checkea si cambió el día y reseta si es necesario."""
        today_now = date.today()
        if today_now != self._today:
            self.reset_daily()

    def _load_state(self) -> None:
        """Carga estado desde JSON si existe."""
        if not self._state_path or not self._state_path.exists():
            return

        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            saved_date = date.fromisoformat(data.get("date", "1970-01-01"))
            today_now = date.today()

            if saved_date == today_now:
                self._daily_pnl = data.get("daily_pnl", 0.0)
                self._meta_reached = data.get("meta_reached", False)
                self._defensive_mode = data.get("defensive_mode", False)
                self._trades_today = data.get("trades_today", 0)
                self._mode_start_time = data.get("mode_since", self._mode_start_time)
            else:
                # Día anterior, reinicia
                self.reset_daily()
        except Exception as e:
            logging.warning("[DailyProfitTracker] Error cargando estado: %s", e)

    def _save_state(self) -> None:
        """Guarda estado a JSON."""
        if not self._state_path:
            return

        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "date": self._today.isoformat(),
                "daily_pnl": self._daily_pnl,
                "daily_target": self._daily_target,
                "meta_reached": self._meta_reached,
                "defensive_mode": self._defensive_mode,
                "trades_today": self._trades_today,
                "mode_since": self._mode_start_time,
            }
            self._state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logging.error("[DailyProfitTracker] Error guardando estado: %s", e)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _get_recommendation(self) -> str:
        """Retorna recomendación de riesgo basada en estado."""
        if self._defensive_mode:
            return "DEFENSIVE: Reduce riesgo, considera stop"
        elif self.progress_pct >= 75:
            return "CAUTIOUS: 75% de meta, reduce riesgo gradualmente"
        elif self.progress_pct >= 50:
            return "NORMAL: 50% de meta, continúa normal"
        else:
            return "AGGRESSIVE: <50% de meta, busca ganancia"
