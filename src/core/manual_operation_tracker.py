"""Manual Operation Tracker — Registra operaciones manuales del usuario.

Permite al usuario registrar entradas manuales (buy/sell) que hace en la UI de Pocket Option
cuando está en una pérdida de Masaniello y quiere intentar recuperación manual.

Flujo:
1. Bot está en LOSS del Masaniello (step 1, 2, ...)
2. Usuario entra manualmente en Pocket Option con su criterio
3. Usuario registra el resultado (WIN/LOSS) via CLI o archivo
4. Sistema actualiza GlobalGaleState y continúa secuencia Masaniello
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from src.core.pipeline import GlobalGaleState
from src.core.session_manager import SessionManager


@dataclass
class ManualOperation:
    """Registro de una operación manual."""
    asset: str
    side: str  # "BUY" o "SELL"
    amount: float
    result: Literal["WIN", "LOSS", "UNKNOWN"]
    balance_before: float
    balance_after: float | None = None
    timestamp: datetime | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class ManualOperationTracker:
    """
    Rastreador de operaciones manuales que impactan el estado de Masaniello.
    
    Responsabilidades:
    - Registrar entrada manual (asset, side, amount)
    - Registrar resultado (WIN/LOSS) tras cierre
    - Actualizar GlobalGaleState y SessionManager
    - Persistir historia para auditoría
    """

    def __init__(
        self,
        global_gale_state: GlobalGaleState,
        session_manager: SessionManager | None = None,
    ) -> None:
        self._gale_state = global_gale_state
        self._session_manager = session_manager
        self._history: list[ManualOperation] = []

    def register_manual_operation(
        self,
        asset: str,
        side: str,
        amount: float,
        balance_before: float,
        result: Literal["WIN", "LOSS", "UNKNOWN"],
        balance_after: float | None = None,
        notes: str = "",
        apply_state: bool = True,
    ) -> ManualOperation:
        """
        Registra una operación manual y actualiza el estado de Masaniello.
        
        Args:
            asset: Ej "EURUSD OTC"
            side: "BUY" o "SELL"
            amount: Cantidad en USD arriesgada
            balance_before: Balance antes de la operación
            result: "WIN", "LOSS" o "UNKNOWN"
            balance_after: Balance después (si se conoce)
            notes: Notas adicionales
        
        Returns:
            ManualOperation registrada
        """
        op = ManualOperation(
            asset=asset,
            side=side,
            amount=amount,
            result=result,
            balance_before=balance_before,
            balance_after=balance_after,
            notes=notes,
        )

        self._history.append(op)

        # Actualizar estado solo cuando este tracker es la fuente del resultado
        # (ej. uso manual por CLI). En deteccion automatica desde engine,
        # apply_state=False para evitar doble conteo.
        if not apply_state:
            return op

        if result == "WIN":
            logging.info(
                "📊 Operación manual WIN registrada: %s %s $%.2f | "
                "Reseteando Masaniello",
                side,
                asset,
                amount,
            )
            self._gale_state.record_win()
            if self._session_manager is not None:
                self._session_manager.update_session_status("WIN")

        elif result == "LOSS":
            loss_amount = balance_before - (balance_after or balance_before - amount)
            logging.info(
                "📊 Operación manual LOSS registrada: %s %s $%.2f | "
                "Pérdida: $%.2f | Continuando Masaniello",
                side,
                asset,
                amount,
                loss_amount,
            )
            self._gale_state.record_loss(amount)
            if self._session_manager is not None:
                self._session_manager.update_session_status(
                    "LOSS",
                    debt_after_loss=self._gale_state.accumulated_loss,
                )

        else:
            logging.warning(
                "📊 Operación manual UNKNOWN registrada: %s %s $%.2f | "
                "No se actualiza Masaniello",
                side,
                asset,
                amount,
            )

        return op

    def get_latest_operation(self) -> ManualOperation | None:
        """Retorna la última operación registrada."""
        return self._history[-1] if self._history else None

    def get_history(self) -> list[ManualOperation]:
        """Retorna historial completo de operaciones manuales."""
        return self._history.copy()

    def detect_balance_change(
        self,
        balance_before: float,
        balance_now: float,
        threshold: float = 0.01,
    ) -> float:
        """
        Detecta si hubo cambio significativo de balance.
        Positivo = ganancia, Negativo = pérdida.
        Retorna 0.0 si no hay cambio significativo.
        """
        diff = balance_now - balance_before
        if abs(diff) > threshold:
            return diff
        return 0.0

    def summary(self) -> dict:
        """Resumen de operaciones manuales en la sesión."""
        if not self._history:
            return {
                "total_operations": 0,
                "wins": 0,
                "losses": 0,
                "unknown": 0,
                "total_risk": 0.0,
                "net_result": 0.0,
            }

        wins = sum(1 for op in self._history if op.result == "WIN")
        losses = sum(1 for op in self._history if op.result == "LOSS")
        unknown = sum(1 for op in self._history if op.result == "UNKNOWN")
        total_risk = sum(op.amount for op in self._history)

        # Calcular resultado neto (si balance_after está disponible)
        net = 0.0
        for op in self._history:
            if op.balance_after is not None:
                net += op.balance_after - op.balance_before

        return {
            "total_operations": len(self._history),
            "wins": wins,
            "losses": losses,
            "unknown": unknown,
            "total_risk": round(total_risk, 2),
            "net_result": round(net, 2),
        }
