"""CLI para registrar operaciones manuales — Interfaz de línea de comandos.

Permite registrar wins/losses manuales mientras el bot está corriendo.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from src.core.manual_operation_tracker import ManualOperationTracker
from src.strategies import manual_strategies


class ManualOperationCLI:
    """CLI para registrar operaciones manuales del usuario."""

    def __init__(
        self,
        tracker: ManualOperationTracker,
        manual_strategy: object | None = None,
    ) -> None:
        self._tracker = tracker
        self._manual_strategy = manual_strategy
        self._running = False

    async def run_interactive_prompt(self) -> None:
        """
        Inicia un prompt interactivo para registrar operaciones.
        Se ejecuta en un thread separado para no bloquear el event loop.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._show_menu)

    def _show_menu(self) -> None:
        """Menú principal (ejecutado en thread separado)."""
        while True:
            print("\n" + "=" * 60)
            print("  📊 REGISTRO DE OPERACIONES MANUALES")
            print("=" * 60)
            print("1. Registrar operación (WIN/LOSS)")
            print("2. Ver última operación")
            print("3. Ver historial completo")
            print("4. Ver resumen de sesión")
            print("5. Salir")
            print("-" * 60)

            choice = input("Selecciona opción (1-5): ").strip()

            if choice == "1":
                self._register_operation()
            elif choice == "2":
                self._show_latest()
            elif choice == "3":
                self._show_history()
            elif choice == "4":
                self._show_summary()
            elif choice == "5":
                print("Cerrando CLI de operaciones manuales...")
                break
            else:
                print("❌ Opción inválida")

    def _register_operation(self) -> None:
        """Registra una nueva operación manual."""
        print("\n" + "-" * 60)
        print("  Registrar Operación Manual")
        print("-" * 60)

        # Entrada de datos
        asset = input("Activo (ej: EURUSD OTC): ").strip().upper() or "EURUSD OTC"
        side = self._prompt_side()
        
        try:
            amount = float(input("Cantidad (USD): ") or "0")
            if amount <= 0:
                print("❌ Cantidad debe ser > 0")
                return
        except ValueError:
            print("❌ Cantidad inválida")
            return

        try:
            balance_before = float(input("Balance ANTES de operación: ") or "0")
            if balance_before <= 0:
                print("❌ Balance debe ser > 0")
                return
        except ValueError:
            print("❌ Balance inválido")
            return

        result = self._prompt_result()

        balance_after = None
        if result != "UNKNOWN":
            try:
                balance_after = float(input("Balance DESPUÉS de operación (opcional): ") or "")
            except ValueError:
                pass

        notes = input("Notas (opcional): ").strip()

        # Registrar
        op = self._tracker.register_manual_operation(
            asset=asset,
            side=side,
            amount=amount,
            balance_before=balance_before,
            result=result,
            balance_after=balance_after,
            notes=notes,
        )

        print(f"\n✅ Operación registrada:")
        print(f"   Activo: {op.asset}")
        print(f"   Tipo: {op.side}")
        print(f"   Cantidad: ${op.amount:.2f}")
        print(f"   Resultado: {op.result}")
        if balance_after is not None:
            diff = balance_after - balance_before
            print(f"   P&L: ${diff:+.2f}")
        print(f"   Timestamp: {op.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    def _show_latest(self) -> None:
        """Muestra la última operación registrada."""
        op = self._tracker.get_latest_operation()
        if op is None:
            print("\n❌ No hay operaciones registradas aún")
            return

        print("\n" + "-" * 60)
        print("  Última Operación")
        print("-" * 60)
        print(f"Activo: {op.asset}")
        print(f"Tipo: {op.side}")
        print(f"Cantidad: ${op.amount:.2f}")
        print(f"Resultado: {op.result}")
        print(f"Balance antes: ${op.balance_before:.2f}")
        if op.balance_after is not None:
            print(f"Balance después: ${op.balance_after:.2f}")
            diff = op.balance_after - op.balance_before
            print(f"P&L: ${diff:+.2f}")
        if op.notes:
            print(f"Notas: {op.notes}")
        print(f"Timestamp: {op.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    def _show_history(self) -> None:
        """Muestra historial completo de operaciones."""
        history = self._tracker.get_history()
        if not history:
            print("\n❌ No hay operaciones registradas aún")
            return

        print("\n" + "-" * 60)
        print(f"  Historial de {len(history)} operaciones")
        print("-" * 60)

        for i, op in enumerate(history, 1):
            result_emoji = "✅" if op.result == "WIN" else "❌" if op.result == "LOSS" else "❓"
            print(
                f"{i}. {result_emoji} {op.asset:12} {op.side:4} ${op.amount:7.2f} "
                f"| {op.timestamp.strftime('%H:%M:%S')}"
            )

    def _show_summary(self) -> None:
        """Muestra resumen de operaciones manuales."""
        summary = self._tracker.summary()

        print("\n" + "-" * 60)
        print("  Resumen de Operaciones Manuales")
        print("-" * 60)
        print(f"Total operaciones: {summary['total_operations']}")
        print(f"  ✅ Wins:    {summary['wins']}")
        print(f"  ❌ Losses:  {summary['losses']}")
        print(f"  ❓ Unknown: {summary['unknown']}")
        print(f"\nRiesgo total: ${summary['total_risk']:.2f}")
        print(f"Resultado neto: ${summary['net_result']:+.2f}")

    @staticmethod
    def _prompt_side() -> str:
        """Pide dirección de operación."""
        while True:
            side = input("Tipo de operación (BUY/SELL): ").strip().upper()
            if side in ("BUY", "SELL"):
                return side
            print("❌ Debe ser BUY o SELL")

    @staticmethod
    def _prompt_result() -> Literal["WIN", "LOSS", "UNKNOWN"]:
        """Pide resultado de operación."""
        print("Resultado de la operación:")
        print("  1. WIN")
        print("  2. LOSS")
        print("  3. UNKNOWN")
        while True:
            choice = input("Selecciona (1-3): ").strip()
            if choice == "1":
                return "WIN"
            elif choice == "2":
                return "LOSS"
            elif choice == "3":
                return "UNKNOWN"
            print("❌ Opción inválida")
