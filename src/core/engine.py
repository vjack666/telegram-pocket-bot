import asyncio
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from src.core.models import TradingSignal
from src.core.console_hub import (
    clear_countdown_line,
    print_countdown_line,
    print_countdown_line_mmss,
    print_order_event,
    print_signal_summary,
)
from src.pocket_option.assets import canonicalize_pocket_asset, normalize_asset_for_compare
from src.pocket_option.client import PocketOptionBaseClient
from src.pocket_option.trade_panel_feed import LiveTradeSnapshot
from src.core.masaniello_manager import MasanielloManager
from src.core.pipeline import MasanielloSessionState, RecoveryProfile
from src.core.equity_bands import EquityBandManager
from src.core.daily_profit_tracker import DailyProfitTracker
from src.utils.session_learning_db import SessionLearningDB
from src.core.manual_operation_tracker import ManualOperationTracker


class SignalEngine:
    def __init__(
        self,
        pocket_client: PocketOptionBaseClient,
        martingale_amounts: list[float],
        martingale_mode: str,
        calc_payout_percent: float,
        calc_increment: int,
        calc_rule10_balance_threshold: float,
        calc_max_steps: int,
        result_grace_seconds: int,
        reference_utc_offset_hours: int,
        color_output: bool,
        signal_late_tolerance_seconds: int,
        global_gale_state: Any,
        masaniello_session: MasanielloSessionState | None = None,
        masaniello_manager: MasanielloManager | None = None,
        recovery_profile: RecoveryProfile | None = None,
        calc_base_balance: float = 300.0,
        event_recorder: Callable[..., None] | None = None,
        equity_manager: Optional[EquityBandManager] = None,
        daily_profit_tracker: Optional[DailyProfitTracker] = None,
        watchdog_trigger: Callable[[], None] | None = None,
        balance_scaling_step: float = 50.0,
        session_base_increment: float | None = None,
        session_base_growth_pct: float = 0.15,
        session_base_max: float = 60.0,
        g2_human_approval: bool = False,
        g2_approval_timeout_seconds: int = 20,
        session_learning_db: SessionLearningDB | None = None,
        operation_mode_schedule_enabled: bool = True,
        operation_mode_hybrid_start_hour: int = 10,
        operation_mode_hybrid_end_hour: int = 21,
        operation_mode_sound_alert: bool = True,
        manual_operation_tracker: ManualOperationTracker | None = None,
    ) -> None:
        self._pocket_client = pocket_client
        self._martingale_amounts = [value for value in martingale_amounts if value > 0] or [2.0, 4.0, 10.0]
        self._martingale_mode = martingale_mode if martingale_mode in {"fixed", "calculator", "masaniello"} else "fixed"
        self._masaniello_session = masaniello_session
        self._masaniello_manager = masaniello_manager
        self._calc_payout = max(0.01, calc_payout_percent / 100.0)
        self._calc_increment = max(1, calc_increment)
        self._calc_rule10_threshold = max(0.0, calc_rule10_balance_threshold)
        self._calc_max_steps = max(1, calc_max_steps)
        self._result_grace_seconds = max(0, result_grace_seconds)
        self._reference_tz = timezone(timedelta(hours=reference_utc_offset_hours))
        self._color_output = color_output
        self._signal_late_tolerance_seconds = max(0, signal_late_tolerance_seconds)
        self._hard_late_execution_seconds = min(10.0, float(self._signal_late_tolerance_seconds))
        self._martingale_prepare_lead_seconds = 30.0
        self._martingale_send_lead_seconds = 0.2
        self._max_entry_delay_seconds = 10.0
        self._calc_base_balance = max(1.0, calc_base_balance)
        # RecoveryProfile: si no se pasa uno, construir defaults seguros desde payout
        if recovery_profile is None:
            full_pm = 1.0 + self._calc_payout
            auto_g1 = round(full_pm / self._calc_payout, 4)
            recovery_profile = RecoveryProfile(
                g1_mult=auto_g1,
                g2_mult=round(auto_g1 * auto_g1, 4),
                max_trade_pct=0.10,
                max_total_exposure_pct=0.25,
            )
        self._recovery_profile = recovery_profile
        self._global_gale = global_gale_state
        self._event_recorder = event_recorder
        self._last_known_balance: float | None = None
        self._equity_manager: Optional[EquityBandManager] = equity_manager
        self._daily_profit_tracker: Optional[DailyProfitTracker] = daily_profit_tracker
        self._watchdog_trigger: Callable[[], None] | None = watchdog_trigger
        self._balance_scaling_step = max(1.0, float(balance_scaling_step))
        self._session_base_increment = (
            None if session_base_increment is None else max(0.01, float(session_base_increment))
        )
        self._session_base_growth_pct = max(0.0, float(session_base_growth_pct))
        self._session_base_max = max(1.0, float(session_base_max))
        self._scaling_anchor_balance: float | None = None
        self._ultimo_resultado: str | None = None
        self._next_entry_override: float | None = None
        # Modo híbrido: freno de aprobación humana en G2
        self._g2_human_approval = g2_human_approval
        self._g2_approval_timeout_seconds = max(5, int(g2_approval_timeout_seconds))
        self._session_learning_db = session_learning_db
        self._operation_mode_schedule_enabled = bool(operation_mode_schedule_enabled)
        self._operation_mode_hybrid_start_hour = max(0, min(23, int(operation_mode_hybrid_start_hour)))
        self._operation_mode_hybrid_end_hour = max(0, min(24, int(operation_mode_hybrid_end_hour)))
        self._operation_mode_sound_alert = bool(operation_mode_sound_alert)
        self._operation_mode_last: str | None = None
        # Estado del ciclo actual para aprendizaje
        self._cycle_sequence: str = ""         # "W", "L", "WL", "LL", etc.
        self._cycle_g2_intervened: bool = False
        self._cycle_g2_approved: bool | None = None
        self._cycle_g2_amount: float | None = None
        if self._masaniello_manager is not None:
            # Precalcular stake inicial para no introducir latencia en el disparo.
            self._next_entry_override = self._masaniello_manager.get_next_stake(None)
            self._log_masaniello_next_entry(self._next_entry_override)
        # CRITICAL FIX: Broker-level lock to serialize all browser operations
        self._broker_lock = asyncio.Lock()
        
        # Log inicial de configuración de modo operativo
        if self._g2_human_approval:
            ventana = f"{self._operation_mode_hybrid_start_hour:02d}:00 - {self._operation_mode_hybrid_end_hour:02d}:00 UTC-3"
            logging.info(
                "[MODO OPERATIVO] G2_HUMAN_APPROVAL=true | HIBRIDO=%s | AUTOMATICO=%s | "
                "ventana_hibrido=%s | alerta_sonora=%s",
                "✓" if self._operation_mode_schedule_enabled else "✗",
                "✓" if self._operation_mode_schedule_enabled else "✗",
                ventana,
                "✓" if self._operation_mode_sound_alert else "✗",
            )
        else:
            logging.info("[MODO OPERATIVO] G2_HUMAN_APPROVAL=false | Siempre AUTOMÁTICO (sin freno G2)")

        # Manual Operation Tracker — para registrar operaciones manuales del usuario
        self._manual_operation_tracker = manual_operation_tracker
        # Flag: el bot está en medio de una operación propia (para ignorar ese cambio de saldo)
        self._bot_trade_in_progress: bool = False

    async def start_balance_monitor(
        self,
        poll_interval: float = 2.5,
        min_change: float = 0.5,
    ) -> None:
        """Loop en background que detecta operaciones manuales por cambio de saldo.

        Cada `poll_interval` segundos lee el saldo. Si detecta un cambio >= `min_change`
        que NO proviene de una operación del bot (flag _bot_trade_in_progress=False),
        lo trata como operación manual: determina WIN/LOSS y actualiza Masaniello.
        No requiere API — usa Playwright igual que el resto del sistema.
        """
        logging.info(
            "[BalanceMonitor] Iniciado — poll=%.1fs | umbral_cambio=$%.2f",
            poll_interval, min_change,
        )
        last_balance: float | None = None
        current: float | None = None
        pending_manual: dict[str, Any] | None = None

        while True:
            try:
                sleep_seconds = 0.5 if pending_manual is not None else poll_interval
                await asyncio.sleep(sleep_seconds)
                current = await self._safe_get_balance()

                if last_balance is None:
                    last_balance = current
                    continue

                diff = current - last_balance

                # Ignorar si el bot está ejecutando una operación propia
                if self._bot_trade_in_progress:
                    self._clear_manual_countdown_inline()
                    pending_manual = None
                    last_balance = current
                    continue

                if pending_manual is not None:
                    result = await self._resolve_pending_manual_result(
                        pending_manual=pending_manual,
                        current_balance=current,
                        min_change=min_change,
                    )
                    if result is not None:
                        self._clear_manual_countdown_inline()
                        await self._apply_manual_result(
                            result=result,
                            balance_before=float(pending_manual["before_balance"]),
                            balance_after=current,
                        )
                        pending_manual = None
                    else:
                        self._render_manual_countdown_inline(pending_manual)
                    continue

                if abs(diff) < min_change:
                    continue

                # Fase 1: detectar apertura manual por debito inicial.
                # En Pocket Option el stake se descuenta al abrir, por eso no se marca LOSS aqui.
                if diff < 0:
                    pending_manual = {
                        "before_balance": last_balance,
                        "opened_balance": current,
                        "opened_at": datetime.now(timezone.utc),
                        "reserved_amount": abs(diff),
                        "expected_close_at": None,
                    }
                    await self._enrich_manual_pending_with_live_snapshot(pending_manual)
                    close_hint = pending_manual.get("expected_close_at")
                    time_source = pending_manual.get("time_source", "desconocida")
                    
                    countdown_str = ""
                    if isinstance(close_hint, datetime):
                        delta = close_hint - datetime.now(timezone.utc)
                        countdown_sec = max(0, int(delta.total_seconds()))
                        mins = countdown_sec // 60
                        secs = countdown_sec % 60
                        countdown_str = f" | CUENTA ATRÁS ⏱️  {mins}:{secs:02d} ({time_source})"
                    else:
                        countdown_str = f" | ⏱️  [no se pudo leer el timer]"
                    
                    logging.info(
                        "[BalanceMonitor] 📊 APERTURA MANUAL DETECTADA | reservado=$%.2f (%.2f → %.2f)%s",
                        abs(diff),
                        last_balance,
                        current,
                        countdown_str,
                    )
                    await self._prefill_manual_loss_amount(pending_manual)
                    self._render_manual_countdown_inline(pending_manual)
                    continue

                # Si solo se observa incremento sin haber visto debito, clasificar como WIN directo.
                await self._apply_manual_result(
                    result="W",
                    balance_before=last_balance,
                    balance_after=current,
                )

            except asyncio.CancelledError:
                self._clear_manual_countdown_inline()
                logging.info("[BalanceMonitor] Monitor detenido.")
                return
            except Exception as exc:
                self._clear_manual_countdown_inline()
                logging.warning("[BalanceMonitor] Error en ciclo de monitoreo: %s", exc)
            finally:
                # Siempre avanzar last_balance para no re-detectar el mismo cambio
                if current is not None:
                    last_balance = current

    async def _enrich_manual_pending_with_live_snapshot(self, pending_manual: dict[str, Any]) -> None:
        try:
            opened_at = pending_manual.get("opened_at")
            selected_asset = await self._pocket_client.get_selected_asset()
            if not selected_asset:
                selected_asset = None
            pending_manual["asset_hint"] = selected_asset or pending_manual.get("asset_hint")

            snapshot = None
            if selected_asset:
                snapshot = await self._pocket_client.get_live_trade_snapshot(
                    selected_asset,
                    side=None,
                    timeout=0.9,
                )

            # Si teníamos snapshot antes y ahora es null → cierre detectado, registrar momento exacto.
            had_snapshot_before = pending_manual.get("had_snapshot_last_poll", False)
            if had_snapshot_before and snapshot is None:
                pending_manual["snapshot_closed_at"] = datetime.now(timezone.utc)
                pending_manual.pop("had_snapshot_last_poll", None)

            if snapshot is not None:
                pending_manual["had_snapshot_last_poll"] = True
                if snapshot.amount is not None:
                    pending_manual["amount_hint"] = float(snapshot.amount)
                if snapshot.forecast_side:
                    pending_manual["side_hint"] = snapshot.forecast_side
                if snapshot.time_remaining_sec is not None:
                    pending_manual["expected_close_at"] = datetime.now(timezone.utc) + timedelta(
                        seconds=max(1, int(snapshot.time_remaining_sec))
                    )
                    pending_manual["time_source"] = "trade_panel_countdown"
                    return
            else:
                pending_manual["had_snapshot_last_poll"] = False

            expiry_seconds = await self._pocket_client.get_configured_expiry_seconds()
            if expiry_seconds is not None and isinstance(opened_at, datetime):
                pending_manual["expected_close_at"] = opened_at + timedelta(seconds=max(1, expiry_seconds))
                pending_manual["time_source"] = "ui_expiry_label"
                # Evitar spam en consola: este refresco corre en cada poll del monitor.
                logging.debug(
                    "[BalanceMonitor] Timer manual por label UI: %ss (cierre_estimado=%s)",
                    expiry_seconds,
                    pending_manual["expected_close_at"].isoformat(),
                )
                return

            if pending_manual.get("no_time_log_emitted") is not True:
                pending_manual["no_time_log_emitted"] = True
                logging.warning(
                    "[BalanceMonitor] No se pudo inferir timer manual ni por panel ni por label de expiración. "
                    "Se usará fallback conservador.",
                )

            if isinstance(opened_at, datetime) and pending_manual.get("expected_close_at") is None:
                pending_manual["expected_close_at"] = opened_at + timedelta(seconds=90)
                pending_manual["time_source"] = "fallback_90s"
        except Exception as exc:
            logging.debug("[BalanceMonitor] No se pudo enriquecer operación manual con snapshot vivo: %s", exc)

    def _render_manual_countdown_inline(self, pending_manual: dict[str, Any]) -> None:
        """Muestra countdown de operación manual en una sola línea sin spamear consola."""
        if not sys.stdout.isatty():
            return

        expected_close_at = pending_manual.get("expected_close_at")
        snapshot_closed_at = pending_manual.get("snapshot_closed_at")
        reserved_amount = float(pending_manual.get("reserved_amount", 0.0) or 0.0)
        amount = float(pending_manual.get("amount_hint", reserved_amount) or reserved_amount)
        asset = str(pending_manual.get("asset_hint") or "MANUAL")
        side = str(pending_manual.get("side_hint") or "MANUAL")

        if snapshot_closed_at is not None:
            remaining_sec = 0
            semaphore = "VERDE LISTO"
        elif isinstance(expected_close_at, datetime):
            remaining_sec = max(0, int((expected_close_at - datetime.now(timezone.utc)).total_seconds()))
            semaphore = "AMARILLO PREPARANDO" if remaining_sec <= max(1, self._result_grace_seconds + 3) else "ROJO ESPERANDO"
        else:
            remaining_sec = 0
            semaphore = "ROJO ESPERANDO"

        mins_total, sec = divmod(remaining_sec, 60)
        print_countdown_line_mmss(
            step_name="MANUAL",
            asset=asset,
            side=side,
            amount=amount,
            mm_total=mins_total,
            ss=sec,
            semaphore=semaphore,
            color_output=self._color_output,
        )

    def _clear_manual_countdown_inline(self) -> None:
        if sys.stdout.isatty():
            clear_countdown_line()

    async def _prefill_manual_loss_amount(self, pending_manual: dict[str, Any]) -> None:
        """Prefill cómodo: escribir monto provisional asumiendo LOSS manual, sin mutar estado real."""
        if self._masaniello_manager is None:
            return
        if pending_manual.get("prefill_loss_written"):
            return

        try:
            next_if_loss = float(self._masaniello_manager.preview_next_stake("L"))
        except Exception as exc:
            logging.debug("[BalanceMonitor] No se pudo previsualizar stake post-loss manual: %s", exc)
            return

        if next_if_loss <= 0:
            return

        try:
            await self._pocket_client.set_amount(next_if_loss, max_retries=2)
            pending_manual["prefill_loss_written"] = True
            pending_manual["prefill_loss_amount"] = next_if_loss
            logging.info(
                "[BalanceMonitor] Prefill manual (si pierde) escrito: $%.2f",
                next_if_loss,
            )
        except Exception as exc:
            logging.warning(
                "[BalanceMonitor] No se pudo escribir prefill manual (si pierde): %s",
                exc,
            )

    async def _resolve_pending_manual_result(
        self,
        pending_manual: dict[str, Any],
        current_balance: float,
        min_change: float,
    ) -> str | None:
        before_balance = float(pending_manual["before_balance"])
        opened_at = pending_manual.get("opened_at")
        expected_close_at = pending_manual.get("expected_close_at")

        # Si podemos leer countdown vivo, refrescar estimacion dinamicamente.
        await self._enrich_manual_pending_with_live_snapshot(pending_manual)
        expected_close_at = pending_manual.get("expected_close_at") or expected_close_at
        snapshot_closed_at = pending_manual.get("snapshot_closed_at")

        diff_vs_before = current_balance - before_balance
        if diff_vs_before > 0.01:
            return "W"

        now_utc = datetime.now(timezone.utc)
        
        # Si detectamos cierre por snapshot=null → usar eso como deadline inmediato.
        if snapshot_closed_at is not None:
            close_deadline = snapshot_closed_at + timedelta(seconds=max(1, self._result_grace_seconds))
            if now_utc >= close_deadline:
                if diff_vs_before < -max(0.01, min_change * 0.5):
                    return "L"
                return None
            else:
                # Aún en gracia post-cierre, esperar un poco más antes de marcar LOSS
                return None
        
        if isinstance(expected_close_at, datetime):
            close_deadline = expected_close_at + timedelta(seconds=max(1, self._result_grace_seconds))
            if now_utc < close_deadline:
                return None
            if diff_vs_before < -max(0.01, min_change * 0.5):
                return "L"
            return None

        # Fallback si no hay countdown legible: no reloj fijo corto; esperar de forma conservadora.
        if isinstance(opened_at, datetime):
            conservative_deadline = opened_at + timedelta(seconds=90)
            if now_utc >= conservative_deadline and diff_vs_before < -max(0.01, min_change * 0.5):
                return "L"

        return None

    async def _apply_manual_result(
        self,
        result: str,
        balance_before: float,
        balance_after: float,
    ) -> None:
        result_label = "WIN" if result == "W" else "LOSS"
        emoji = "✅ GANADA" if result == "W" else "❌ PERDIDA"
        diff = balance_after - balance_before
        logging.info(
            "[BalanceMonitor] ⏸️  CIERRE DETECTADO - Operación manual %s | cambio=$%.2f "
            "(saldo: %.2f → %.2f)",
            emoji,
            diff,
            balance_before,
            balance_after,
        )
        self._emit_event(
            "manual_trade_detected",
            result=result,
            balance_before=balance_before,
            balance_after=balance_after,
            diff=diff,
        )

        if self._masaniello_manager is not None:
            self._ultimo_resultado = result
            self._next_entry_override = self._masaniello_manager.get_next_stake(result)
            self._log_masaniello_next_entry(self._next_entry_override)
            if self._next_entry_override and self._next_entry_override > 0:
                try:
                    await self._pocket_client.set_amount(self._next_entry_override, max_retries=2)
                    logging.info(
                        "[BalanceMonitor] Siguiente monto Masaniello escrito: $%.2f",
                        self._next_entry_override,
                    )
                except Exception as exc:
                    logging.warning(
                        "[BalanceMonitor] No se pudo escribir monto en broker: %s", exc
                    )

    async def execute_signal(self, signal: TradingSignal) -> None:
        self._bot_trade_in_progress = True
        try:
            await self._run_martingale_flow(signal)
        finally:
            self._bot_trade_in_progress = False

    async def _run_martingale_flow(self, signal: TradingSignal) -> None:
        before_cycle_balance = await self._safe_get_balance()
        start_step = 0
        operation_mode = self._refresh_operation_mode(signal)

        # Toda señal nueva entra por ENTRADA (step=0).
        # Si hay pérdidas acumuladas de señales anteriores, se conservan para el cálculo de montos.
        self._global_gale.reset_for_new_signal(before_cycle_balance)
        # Resetear estado de aprendizaje del ciclo
        self._cycle_sequence = ""
        self._cycle_g2_intervened = False
        self._cycle_g2_approved = None
        self._cycle_g2_amount = None
        logging.info(
            "Nueva señal inicia en ENTRADA (step=0) | balance=%.2f accumulated_loss=%.2f target=%.2f",
            before_cycle_balance,
            self._global_gale.accumulated_loss,
            self._global_gale.target_balance,
        )
        
        cycle_amounts = self._build_cycle_amounts(before_cycle_balance)
        entry_times = self._build_schedule(signal, len(cycle_amounts))
        self._print_waiting_summary(signal, entry_times, cycle_amounts)
        self._emit_event(
            "trade_cycle_started",
            asset=signal.asset,
            side=signal.side,
            expiry_minutes=signal.expiry_minutes,
            source_name=signal.source_name or "",
            amount_plan=cycle_amounts,
            schedule=[item.isoformat() for item in entry_times],
            gale_step_start=start_step,
            gale_target_balance=self._global_gale.target_balance,
        )

        logging.info(
            "━━━ Flujo señal: %s %s exp=%sm modo=%s ━━━\n"
            "  Modo operativo: %s\n"
            "  Horarios sesión: %s → %s\n"
            "  Montos: %s",
            signal.asset,
            signal.side,
            signal.expiry_minutes,
            self._martingale_mode,
            f"🔴 {operation_mode}" if operation_mode == "HIBRIDO" else f"🟢 {operation_mode}",
            (signal.session_start_utc.strftime("%H:%M UTC") if signal.session_start_utc else "N/A"),
            (signal.session_end_utc.strftime("%H:%M UTC") if signal.session_end_utc else "N/A"),
            cycle_amounts,
        )

        await self._execute_step_chain(signal, cycle_amounts, entry_times, step_idx=start_step)

        # Guardar registro de aprendizaje al finalizar el ciclo
        self._save_learning_record(signal, cycle_amounts)

        # Actualizar capital operativo dinámico tras cada ciclo completo
        await self._apply_equity_update()
        await self.check_balance_scaling()

    def _allowed_entry_delay_seconds(self, step_name: str) -> float:
        """Ventana máxima de atraso permitida por tipo de paso.

        ENTRADA conserva una ventana estricta. MARTINGALAS permiten margen extra
        para absorber latencia de resolución WIN/LOSS del broker.
        """
        if step_name.startswith("MARTINGALA"):
            return max(self._max_entry_delay_seconds, float(self._result_grace_seconds) + 1.0)
        return self._max_entry_delay_seconds

    def _save_learning_record(self, signal: TradingSignal, cycle_amounts: list[float]) -> None:
        if self._session_learning_db is None:
            return
        seq = self._cycle_sequence or ""
        label = self._masaniello_label_from_seq(seq)
        step_reached = len(seq)
        won = seq.endswith("W") if seq else False
        # Calcular pnl del ciclo aproximado desde el global_gale
        session_pnl = 0.0
        try:
            if won and cycle_amounts:
                step_idx = max(0, step_reached - 1)
                amt = cycle_amounts[min(step_idx, len(cycle_amounts) - 1)]
                session_pnl = round(amt * self._calc_payout - sum(cycle_amounts[:step_idx]), 4)
            else:
                session_pnl = round(-sum(cycle_amounts[:step_reached]), 4)
        except Exception:
            pass
        self._session_learning_db.record_session(
            asset=signal.asset,
            side=signal.side,
            masaniello_sequence=seq,
            masaniello_label=label,
            step_reached=step_reached,
            g2_intervened=self._cycle_g2_intervened,
            g2_approved=self._cycle_g2_approved,
            g2_amount=self._cycle_g2_amount,
            session_pnl=session_pnl,
            won=won,
            expiry_minutes=signal.expiry_minutes,
        )

    @staticmethod
    def _masaniello_label_from_seq(seq: str) -> str:
        if not seq:
            return "M1 inicio"
        if seq == "W":
            return "M2 tras W"
        if seq == "L":
            return "M2 tras L"
        if seq in {"WL", "LW"}:
            return "M3 con 1W1L"
        if seq == "LL":
            return "M3 tras 2L"
        return f"M{len(seq)+1} extensión"

    async def _apply_equity_update(self) -> None:
        """Lee el balance actual y notifica al EquityBandManager.

        Si la banda cambia, sincroniza _calc_base_balance y la base de
        MasanielloSessionState para que el próximo ciclo use el sizing correcto.
        """
        if self._equity_manager is None:
            return
        try:
            balance = await self._safe_get_balance()
        except Exception as exc:
            logging.warning("[EquityBands] No se pudo leer balance para actualización: %s", exc)
            return

        changed = self._equity_manager.notify_balance(balance)
        if changed:
            new_base = self._equity_manager.operational_base
            self._calc_base_balance = new_base
            if self._masaniello_session is not None:
                self._masaniello_session.update_base(new_base)
            self._emit_event(
                "equity_band_changed",
                balance=balance,
                new_operational_base=new_base,
                daily_target=self._equity_manager.daily_target,
                **self._equity_manager.status(),
            )
            logging.info(
                "[EquityBands] Base operativa actualizada → %.2f | "
                "meta_diaria=%.2f | balance=%.2f",
                new_base,
                self._equity_manager.daily_target,
                balance,
            )
        else:
            logging.debug(
                "[EquityBands] Sin cambio de banda (balance=%.2f base=%.2f)",
                balance,
                self._equity_manager.operational_base,
            )

    async def _execute_step_chain(
        self,
        signal: TradingSignal,
        cycle_amounts: list[float],
        entry_times: list[datetime],
        step_idx: int,
        pre_clicked: tuple[float, float | None, datetime] | None = None,
    ) -> None:
        step_name = "ENTRADA" if step_idx == 0 else f"MARTINGALA {step_idx}"
        amount = cycle_amounts[step_idx]
        entry_at = entry_times[step_idx]

        if pre_clicked is None:
            before_balance, entry_price, click_at = await self._prepare_and_click_step(
                step_name,
                signal,
                entry_at,
                amount,
            )
        else:
            before_balance, entry_price, click_at = pre_clicked

        close_at = click_at + timedelta(minutes=signal.expiry_minutes)
        entry_delay = (click_at - entry_at).total_seconds()
        logging.info(
            "%s tiempos reales: entry_programada=%s click_real=%s expiry_real=%s exp=%sm delay=%.1fs",
            step_name,
            entry_at.isoformat(),
            click_at.isoformat(),
            close_at.isoformat(),
            signal.expiry_minutes,
            entry_delay,
        )

        is_last = step_idx >= len(cycle_amounts) - 1
        if is_last:
            won = await self._monitor_order_result_until_close(
                before_balance,
                close_at,
                signal.asset,
                signal.side,
                entry_delay=entry_delay,
            )
            if won is None:
                logging.warning(
                    "Resultado desconocido en %s (delay=%.1fs). No se registra WIN ni LOSS. "
                    "La martingala no avanzará.",
                    step_name,
                    entry_delay,
                )
                return
            if won:
                logging.info("Resultado: WIN en %s. Se detiene martingala.", step_name)
                self._cycle_sequence += "W"  # tracking de aprendizaje
                self._global_gale.record_win()
                if self._masaniello_session is not None:
                    self._masaniello_session.record_win()
                self._update_masaniello_manager_after_result("W")
                
                # Registrar en daily profit tracker
                pnl_win = round(amount * self._calc_payout, 2)
                if self._daily_profit_tracker is not None:
                    tracker_status = self._daily_profit_tracker.record_trade(pnl_win)
                    self._emit_event("daily_profit_update", **tracker_status)
                    if tracker_status.get("meta_just_reached"):
                        logging.info("[Daily Meta] ✓ META ALCANZADA: $%.2f | Modo DEFENSIVO activado", 
                                   tracker_status.get("daily_pnl", 0))
                
                self._emit_event(
                    "trade_result_win",
                    asset=signal.asset,
                    side=signal.side,
                    step_name=step_name,
                    amount=amount,
                    close_at=close_at.isoformat(),
                    source_name=signal.source_name or "",
                )
                print_order_event(
                    "win",
                    step_name,
                    signal.asset,
                    signal.side,
                    amount,
                    color_output=self._color_output,
                )
                # Watchdog: escaneo post-trade con delay de 5s
                if self._watchdog_trigger is not None:
                    self._watchdog_trigger()
            else:
                logging.warning("Resultado: LOSS en %s. Gale continuará en siguiente señal.", step_name)
                self._cycle_sequence += "L"  # tracking de aprendizaje
                self._global_gale.record_loss(amount)
                if self._masaniello_session is not None:
                    self._masaniello_session.record_loss()
                self._update_masaniello_manager_after_result("L")
                
                # Registrar en daily profit tracker (pérdida)
                pnl_loss = round(-amount, 2)
                if self._daily_profit_tracker is not None:
                    tracker_status = self._daily_profit_tracker.record_trade(pnl_loss)
                    self._emit_event("daily_profit_update", **tracker_status)
                    # Log del progreso hacia la meta
                    logging.info("[Daily Meta] Progreso: $%.2f / $%.2f (%.1f%%) | Modo: %s",
                               tracker_status.get("daily_pnl", 0),
                               tracker_status.get("daily_target", 0),
                               tracker_status.get("progress_pct", 0),
                               "DEFENSIVO" if tracker_status.get("defensive_mode") else "NORMAL")
                
                self._emit_event(
                    "trade_result_loss",
                    asset=signal.asset,
                    side=signal.side,
                    step_name=step_name,
                    amount=amount,
                    close_at=close_at.isoformat(),
                    source_name=signal.source_name or "",
                )
                print_order_event(
                    "loss",
                    step_name,
                    signal.asset,
                    signal.side,
                    amount,
                    color_output=self._color_output,
                )
                # Watchdog: escaneo post-trade con delay de 5s
                if self._watchdog_trigger is not None:
                    self._watchdog_trigger()
            return

        next_info = await self._monitor_and_arm_next_step(
            signal=signal,
            current_step_name=step_name,
            current_close_at=close_at,
            current_before_balance=before_balance,
            current_entry_price=entry_price,
            current_amount=amount,
            current_entry_delay=entry_delay,
            next_step_idx=step_idx + 1,
            next_entry_at=entry_times[step_idx + 1],
            next_amount=cycle_amounts[step_idx + 1],
        )
        if next_info is None:
            return

        await self._execute_step_chain(
            signal,
            cycle_amounts,
            entry_times,
            step_idx + 1,
            pre_clicked=next_info,
        )

    async def _prepare_and_click_step(
        self,
        step_name: str,
        signal: TradingSignal,
        entry_at: datetime,
        amount: float,
    ) -> tuple[float, float | None, datetime]:
        if not await self._wait_until_scheduled(step_name, signal, entry_at, amount):
            raise RuntimeError(f"{step_name} cancelada antes del click")

        # CRITICAL FIX: Validate entry time hasn't expired before executing
        # This applies to ALL steps (ENTRADA and MARTINGALAS)
        now_utc = datetime.now(timezone.utc)
        delay_from_entry = (now_utc - entry_at).total_seconds()
        max_entry_delay = self._allowed_entry_delay_seconds(step_name)
        if delay_from_entry > max_entry_delay:
            logging.error(
                "%s cancelada: entrada ya vencida (%.1f segundos pasados, máx=%.1f). Ignorar.",
                step_name,
                delay_from_entry,
                max_entry_delay,
            )
            raise RuntimeError(f"{step_name} cancelada: entrada pasó hace {delay_from_entry:.1f}s")

        # Fast path: do not block click for long balance/price reads.
        before_balance = await self._get_pre_click_balance_fast()
        entry_price = None

        logging.info(
            "%s EJECUTANDO CLICK a T-3s: %s %s amount=%.2f entry_price=%s | time_ok=true delay=%.1fs",
            step_name,
            signal.asset,
            signal.side,
            amount,
            f"{entry_price:.5f}" if entry_price is not None else "n/d",
            delay_from_entry,
        )

        # CRITICAL FIX: Broker lock wraps ONLY the critical zone (validate + click)
        # NOT the long operations like prepare_order_for_execution
        # Keep lock duration SHORT (milliseconds, not seconds)
        async with self._broker_lock:
            # Revalidate asset quickly INSIDE lock (should be fast, just a read from DOM)
            if not await self._validate_asset_before_click(signal.asset, step_name):
                logging.warning(
                    "%s desalineada. Intentando realinear activo/monto/exp FUERA del lock, luego revalidar.",
                    step_name,
                )
                # OPTIMIZATION: Do the slow realignment OUTSIDE the lock
                raise RuntimeError(f"{step_name} cancelada por desalineacion de activo")

            try:
                await self._pocket_client.execute_order_click(signal.side)
            except Exception as exc:
                logging.exception(
                    "Fallo click de orden [%s] %s %s: %s",
                    step_name,
                    signal.asset,
                    signal.side,
                    exc,
                )
                print_order_event(
                    "error",
                    step_name,
                    signal.asset,
                    signal.side,
                    amount,
                    extra=str(exc),
                    color_output=self._color_output,
                )
                raise

        click_at = datetime.now(timezone.utc)

        logging.info("%s ejecutada: %s %s amount=%.2f", step_name, signal.asset, signal.side, amount)
        self._emit_event(
            "trade_step_executed",
            asset=signal.asset,
            side=signal.side,
            step_name=step_name,
            amount=amount,
            click_at=click_at.isoformat(),
            entry_at=entry_at.isoformat(),
            source_name=signal.source_name or "",
        )
        print_order_event(
            "executed",
            step_name,
            signal.asset,
            signal.side,
            amount,
            color_output=self._color_output,
        )
        return before_balance, entry_price, click_at

    async def _monitor_and_arm_next_step(
        self,
        signal: TradingSignal,
        current_step_name: str,
        current_close_at: datetime,
        current_before_balance: float,
        current_entry_price: float | None,
        current_amount: float,
        current_entry_delay: float,
        next_step_idx: int,
        next_entry_at: datetime,
        next_amount: float,
    ) -> tuple[float, float | None, datetime] | None:
        next_step_name = f"MARTINGALA {next_step_idx}"
        next_prepared = False
        prepare_lead_seconds = max(self._martingale_prepare_lead_seconds, 20.0)
        last_monitor_second: int | None = None

        # Nuevo comportamiento: prearmado inmediato del siguiente gale tras ejecutar el paso actual.
        # No dispara click; solo deja activo/monto listos para ganar margen ante latencia de broker/red.
        try:
            logging.info(
                "[PRE-GALE] Preparando %s inmediatamente: %s | Monto: $%.2f",
                next_step_name,
                signal.asset,
                next_amount,
            )
            await self._pocket_client.prepare_order_for_execution(
                signal.asset,
                next_amount,
                signal.expiry_minutes,
                max_retries=3,
            )
            next_prepared = True
            logging.info(
                "[PRE-GALE] %s lista con anticipacion total (click se mantiene en T-3s si hay LOSS).",
                next_step_name,
            )
        except Exception as exc:
            logging.warning(
                "[PRE-GALE] Preparacion inmediata de %s fallo (%s). Se reintentara en ventana final.",
                next_step_name,
                exc,
            )

        while True:
            now_utc = datetime.now(timezone.utc)
            seconds_to_close = (current_close_at - now_utc).total_seconds()
            seconds_to_next_entry = (next_entry_at - now_utc).total_seconds()
            if seconds_to_close <= 0:
                break

            last_monitor_second = int(max(0, seconds_to_close))

            if not next_prepared and seconds_to_next_entry <= prepare_lead_seconds:
                logging.info(
                    "%s entrando en ventana final. Preparando %s con monto %.2f (faltan %.1fs para entrada programada)",
                    current_step_name,
                    next_step_name,
                    next_amount,
                    seconds_to_next_entry,
                )
                await self._pocket_client.prepare_order_for_execution(
                    signal.asset,
                    next_amount,
                    signal.expiry_minutes,
                    max_retries=3,
                )
                next_prepared = True

            await asyncio.sleep(0.25)

        clear_countdown_line()

        won = await self._monitor_order_result_until_close(
            current_before_balance,
            current_close_at,
            signal.asset,
            signal.side,
            entry_delay=current_entry_delay,
        )
        if won is None:
            logging.warning(
                "Resultado desconocido en %s (delay=%.1fs). No se ejecutará martingala.",
                current_step_name,
                current_entry_delay,
            )
            return None
        if won:
            logging.info("Resultado: WIN en %s. Se detiene martingala.", current_step_name)
            self._cycle_sequence += "W"  # tracking de aprendizaje
            self._global_gale.record_win()
            if self._masaniello_session is not None:
                self._masaniello_session.record_win()
            self._update_masaniello_manager_after_result("W")
            self._emit_event(
                "trade_result_win",
                asset=signal.asset,
                side=signal.side,
                step_name=current_step_name,
                amount=current_amount,
                close_at=current_close_at.isoformat(),
                source_name=signal.source_name or "",
            )
            print_order_event(
                "win",
                current_step_name,
                signal.asset,
                signal.side,
                next_amount,
                color_output=self._color_output,
            )
            return None

        logging.info("%s cerro en LOSS. Continuando con %s.", current_step_name, next_step_name)
        self._cycle_sequence += "L"  # tracking de aprendizaje
        # Registrar LOSS en el estado global antes de continuar al siguiente paso
        # Nota: NO se registra en MasanielloSession aqui — es un paso intermedio.
        # Solo se registra como LOSS de señal cuando es_last=True en _execute_step_chain.
        self._global_gale.record_loss(current_amount)

        # ── G2 APPROVAL DESHABILITADO ──────────────────────────────────────
        # El usuario confirma W/L después del cierre, no antes.
        # Se comentó la aprobación previa de G2 para evitar prompt conflictante.
        # if self._should_request_g2_approval(signal=signal, refresh_mode=True) and next_step_idx == 2:
        #     approved = await self._request_g2_approval(signal, next_amount)
        # ───────────────────────────────────────────────────────────────────

        try:
            if next_prepared:
                return await self._click_prepared_step_immediate(
                    next_step_name,
                    signal,
                    next_entry_at,
                    next_amount,
                )

            # Fallback: si por alguna razón no se alcanzó a prearmar, usar flujo completo.
            return await self._prepare_and_click_step(
                next_step_name,
                signal,
                next_entry_at,
                next_amount,
            )
        except RuntimeError as exc:
            logging.warning("%s cancelada tras LOSS previo: %s", next_step_name, exc)
            return None

    def _current_operation_mode(self, signal: TradingSignal | None = None) -> str:
        """Define el modo actual considerando horarios de sesión de la señal o ventana por defecto.
        
        Prioridad:
        1. Si la señal tiene horarios de sesión, usarlos (noche = automático, día = híbrido)
        2. Si no, usar ventana configurada de operación horaria
        3. Si no hay aprobación G2 configurada, siempre automático
        
        Si solo se tiene session_start_utc (sin end), asumir duración de ~8 horas.
        """
        if not self._g2_human_approval:
            return "AUTOMATICO"
        if not self._operation_mode_schedule_enabled:
            return "HIBRIDO"

        now_utc = datetime.now(timezone.utc)
        now_ref = now_utc.astimezone(self._reference_tz)
        
        # Si la señal tiene al menos hora de inicio de sesión, usarla
        if signal is not None and signal.session_start_utc is not None:
            session_end = signal.session_end_utc
            # Si no hay fin explícito, asumir sesión de ~8 horas
            if session_end is None:
                session_end = signal.session_start_utc + timedelta(hours=8)
            
            # Si estamos dentro de la sesión → AUTOMÁTICO (noche)
            # Si está fuera → HIBRIDO (día)
            is_in_session = signal.session_start_utc <= now_utc < session_end
            return "AUTOMATICO" if is_in_session else "HIBRIDO"
        
        # Fallback: usar ventana configurada por hora
        hour = now_ref.hour
        start = self._operation_mode_hybrid_start_hour
        end = self._operation_mode_hybrid_end_hour

        # Ventana normal [start, end): ejemplo 10:00-21:00 = HIBRIDO
        if start < end:
            return "HIBRIDO" if start <= hour < end else "AUTOMATICO"
        # Ventana cruzando medianoche (por robustez)
        return "HIBRIDO" if (hour >= start or hour < end) else "AUTOMATICO"

    def _refresh_operation_mode(self, signal: TradingSignal | None = None) -> str:
        mode = self._current_operation_mode(signal)
        if mode == self._operation_mode_last:
            return mode

        previous = self._operation_mode_last or "INICIAL"
        self._operation_mode_last = mode
        now_utc = datetime.now(timezone.utc)
        now_ref = now_utc.astimezone(self._reference_tz)
        
        # Info sobre fuente de decisión
        source = "horarios_sesion" if (signal is not None and signal.session_start_utc is not None) else "ventana_configurada"
        emoji_prev = "🔴" if previous == "HIBRIDO" else ("🟢" if previous == "AUTOMATICO" else "⚪")
        emoji_new = "🔴" if mode == "HIBRIDO" else "🟢"
        
        logging.info(
            "╔════════════════════════════════════════════════════════╗\n"
            "║ [CAMBIO DE MODO OPERATIVO]                             ║\n"
            "║ %s %s → %s %s                                          ║\n"
            "║ Hora de señales: %02d:%02d UTC-3                        ║\n"
            "║ Fuente: %s                                           ║\n"
            "╚════════════════════════════════════════════════════════╝",
            emoji_prev,
            previous,
            emoji_new,
            mode,
            now_ref.hour,
            now_ref.minute,
            source,
        )
        self._emit_event(
            "operation_mode_changed",
            previous_mode=previous,
            current_mode=mode,
            signal_hour=f"{now_ref.hour:02d}:{now_ref.minute:02d}",
            hybrid_window_start=self._operation_mode_hybrid_start_hour,
            hybrid_window_end=self._operation_mode_hybrid_end_hour,
            source=source,
        )
        if self._operation_mode_sound_alert:
            # Distinto patrón: 2 beeps al volver a HIBRIDO, 1 beep para AUTOMATICO.
            # Usar winsound en Windows para garantizar sonido (print("\a") no es confiable)
            try:
                if sys.platform == "win32":
                    import winsound
                    import time
                    frequency = 1000  # Hz
                    duration = 200    # ms
                    beeps = 2 if mode == "HIBRIDO" else 1
                    for _ in range(beeps):
                        winsound.Beep(frequency, duration)
                        if beeps > 1:
                            time.sleep(0.1)
                else:
                    print("\a\a" if mode == "HIBRIDO" else "\a", end="", flush=True)
            except Exception as exc:
                logging.debug("Error sonoro en cambio de modo: %s", exc)
        return mode

    def _should_request_g2_approval(self, signal: TradingSignal | None = None, refresh_mode: bool = False) -> bool:
        mode = self._refresh_operation_mode(signal) if refresh_mode else (self._operation_mode_last or self._current_operation_mode(signal))
        return mode == "HIBRIDO"

    async def _request_g2_approval(self, signal: TradingSignal, amount: float) -> bool:
        """Compatibilidad sin prompts: G2 se aprueba automáticamente."""
        logging.info(
            "[HÍBRIDO] Aprobación G2 automática (sin prompt). Activo=%s Dirección=%s Monto=%.2f",
            signal.asset,
            signal.side,
            amount,
        )
        return True

    async def _click_prepared_step_immediate(
        self,
        step_name: str,
        signal: TradingSignal,
        entry_at: datetime,
        amount: float,
    ) -> tuple[float, float | None, datetime]:
        """Ejecuta un paso YA prearmado sin re-preparar ni revalidar activo."""
        now_utc = datetime.now(timezone.utc)
        delay_from_entry = (now_utc - entry_at).total_seconds()
        max_entry_delay = self._allowed_entry_delay_seconds(step_name)
        if delay_from_entry > max_entry_delay:
            raise RuntimeError(
                f"{step_name} cancelada: click inmediato fuera de ventana ({delay_from_entry:.1f}s > {max_entry_delay:.1f}s)"
            )

        before_balance = await self._get_pre_click_balance_fast()
        entry_price = None

        logging.info(
            "%s CLICK INMEDIATO (prearmada): %s %s amount=%.2f entry_price=%s delay=%.1fs",
            step_name,
            signal.asset,
            signal.side,
            amount,
            f"{entry_price:.5f}" if entry_price is not None else "n/d",
            delay_from_entry,
        )

        async with self._broker_lock:
            try:
                await self._pocket_client.execute_order_click(signal.side)
            except Exception as exc:
                logging.exception(
                    "Fallo click inmediato [%s] %s %s: %s",
                    step_name,
                    signal.asset,
                    signal.side,
                    exc,
                )
                print_order_event(
                    "error",
                    step_name,
                    signal.asset,
                    signal.side,
                    amount,
                    extra=str(exc),
                    color_output=self._color_output,
                )
                raise

        click_at = datetime.now(timezone.utc)
        logging.info("%s ejecutada (prearmada): %s %s amount=%.2f", step_name, signal.asset, signal.side, amount)
        self._emit_event(
            "trade_step_executed",
            asset=signal.asset,
            side=signal.side,
            step_name=step_name,
            amount=amount,
            click_at=click_at.isoformat(),
            entry_at=entry_at.isoformat(),
            source_name=signal.source_name or "",
        )
        print_order_event(
            "executed",
            step_name,
            signal.asset,
            signal.side,
            amount,
            color_output=self._color_output,
        )
        return before_balance, entry_price, click_at

    def _build_schedule(self, signal: TradingSignal, count: int) -> list[datetime]:
        now_utc = datetime.now(timezone.utc)
        schedule: list[datetime] = []

        first = signal.execute_at_utc or now_utc
        schedule.append(first)

        mg_times = list(signal.martingale_execute_at_utc)
        for idx in range(1, count):
            if idx - 1 < len(mg_times):
                schedule.append(mg_times[idx - 1])
                continue
            schedule.append(schedule[-1] + timedelta(minutes=signal.expiry_minutes))

        return schedule

    async def _wait_until_scheduled(
        self,
        step_name: str,
        signal: TradingSignal,
        execute_at: datetime,
        amount: float,
    ) -> bool:
        now_utc = datetime.now(timezone.utc)
        delay = (execute_at - now_utc).total_seconds()
        prepare_lead_seconds, send_lead_seconds = self._dynamic_timing_leads(signal.expiry_minutes)

        # ENTRADA: preparar desde que llega la señal y disparar 3s antes del inicio de la vela.
        eager_prepare = step_name == "ENTRADA"
        if eager_prepare:
            send_lead_seconds = 3.0

        if delay > send_lead_seconds:
            logging.info(
                "%s programada: %s %s entra a %s UTC (en %.1fs) | semaforo prep=%.1fs envio=%.1fs",
                step_name,
                signal.asset,
                signal.side,
                execute_at.isoformat(),
                delay,
                prepare_lead_seconds,
                send_lead_seconds,
            )
            try:
                await self._run_countdown_and_prepare(
                    step_name,
                    signal,
                    execute_at,
                    amount,
                    prepare_lead_seconds,
                    send_lead_seconds,
                    eager_prepare=eager_prepare,
                )
            except RuntimeError as exc:
                # CRITICAL FIX: If asset not available, cancel signal immediately
                if "ACTIVO_NO_DISPONIBLE" in str(exc):
                    logging.warning(
                        "%s cancelada: activo %s no disponible en broker",
                        step_name,
                        signal.asset,
                    )
                    raise RuntimeError(f"{step_name} cancelada: activo no disponible")
                raise
            return True

        threshold = float(self._signal_late_tolerance_seconds)
        threshold = self._hard_late_execution_seconds
        if delay < -threshold:
            logging.info(
                "%s ignorada por atraso (%.1fs): %s %s",
                step_name,
                abs(delay),
                signal.asset,
                signal.side,
            )
            return False

        if delay < 0:
            logging.info(
                "%s en atraso controlado (%.1fs) dentro de tolerancia. Continuando con ejecucion inmediata.",
                step_name,
                abs(delay),
            )

        # Señal en ventana inmediata: puede no haber pasado por _run_countdown_and_prepare.
        # Forzamos preparación rápida para no clickear con el activo/monto anterior.
        from src.core.console_hub import print_countdown_line, clear_countdown_line
        print_countdown_line(
            step_name=step_name,
            asset=signal.asset,
            side=signal.side,
            amount=amount,
            hh=0, mm=0, ss=0,
            semaphore="VERDE LISTO",
            color_output=self._color_output,
        )
        try:
            await self._pocket_client.prepare_order_for_execution(
                signal.asset,
                amount,
                signal.expiry_minutes,
                max_retries=2,
            )
        except Exception as exc:
            clear_countdown_line()
            logging.warning(
                "%s cancelada: no se pudo preparar orden en ventana inmediata: %s",
                step_name,
                exc,
            )
            return False

        return True

    def _emit_event(self, event: str, **fields: Any) -> None:
        if self._event_recorder is None:
            return
        try:
            self._event_recorder(event, component="trade_engine", **fields)
        except Exception:
            pass

    async def _validate_asset_before_click(self, expected_asset: str, step_name: str) -> bool:
        """
        Validate that the currently selected asset matches the expected asset.
        Uses strict normalization (remove slashes, spaces, OTC) for comparison.
        """
        try:
            current_asset = await self._pocket_client.get_selected_asset()
        except Exception as exc:
            logging.warning("%s no pudo validar activo previo al click: %s", step_name, exc)
            return True

        if not current_asset:
            return True

        def normalize_strict(asset: str) -> str:
            """Strict normalization: remove slashes, spaces, OTC, uppercase."""
            return (
                (asset or "")
                .upper()
                .replace("/", "")
                .replace(" ", "")
                .replace("OTC", "")
                .strip()
            )

        expected = canonicalize_pocket_asset(expected_asset, default_asset="")
        current = canonicalize_pocket_asset(current_asset, default_asset="")
        
        # CRITICAL FIX: Normalize BOTH sides strictly before comparison
        expected_norm = normalize_strict(expected)
        current_norm = normalize_strict(current)
        
        logging.info(
            "%s comparando activos: actual=%s (norm=%s) esperado=%s (norm=%s)",
            step_name,
            current,
            current_norm,
            expected,
            expected_norm,
        )
        
        if expected_norm and current_norm and expected_norm != current_norm:
            logging.error(
                "%s cancelada: activo actual '%s' (norm=%s) != esperado '%s' (norm=%s)",
                step_name,
                current,
                current_norm,
                expected,
                expected_norm,
            )
            return False
        return True

    async def _attempt_realign_before_click(
        self,
        step_name: str,
        signal: TradingSignal,
        amount: float,
    ) -> bool:
        try:
            await self._pocket_client.prepare_order_for_execution(
                signal.asset,
                amount,
                signal.expiry_minutes,
                max_retries=1,
            )
            logging.info("%s realineada antes del click", step_name)
            return True
        except RuntimeError as exc:
            # CRITICAL FIX: If asset not available, propagate immediately (don't retry)
            if "ACTIVO_NO_DISPONIBLE" in str(exc):
                logging.error(
                    "%s no puede realinearse: activo %s no disponible en broker",
                    step_name,
                    signal.asset,
                )
                raise
            logging.warning("%s no pudo realinearse antes del click: %s", step_name, exc)
            return False
        except Exception as exc:
            logging.warning("%s no pudo realinearse antes del click: %s", step_name, exc)
            return False

    async def _monitor_order_result_until_close(
        self,
        before_balance: float,
        close_at: datetime,
        asset: str,
        side: str,
        entry_delay: float = 0.0,
    ) -> bool | None:
        """Espera 1 segundo después del cierre y detecta WIN/LOSS por balance.

        Retorna True (WIN), False (LOSS) o None (resultado desconocido).
        """
        # Iniciar chequeo apenas pasado el cierre y sondear durante una pequeña ventana.
        wait_until = close_at + timedelta(seconds=0.2)
        now_utc = datetime.now(timezone.utc)
        remaining = (wait_until - now_utc).total_seconds()

        logging.info(
            "Esperando cierre: asset=%s side=%s close_at=%s (%.1fs restantes) delay=%.1fs",
            asset,
            side,
            close_at.isoformat(),
            max(0.0, remaining),
            entry_delay,
        )

        if remaining > 0:
            await asyncio.sleep(remaining)

        # ════════════════════════════════════════════════════════════════════
        # DECISIÓN: ¿Confiar en DOM o usar BALANCE?
        # ════════════════════════════════════════════════════════════════════

        # Leer balance en sondeo corto hasta detectar cambio o agotar gracia.
        # Seguridad anti-falso-loss: un diff negativo no dispara LOSS inmediato.
        # Se confirma LOSS solo al final de la ventana por posibles retrasos de settlement.
        try:
            grace_seconds = max(1, self._result_grace_seconds)
            poll_deadline = datetime.now(timezone.utc) + timedelta(seconds=grace_seconds)
            last_diff = 0.0
            negative_streak = 0
            early_loss_polls_required = 2
            while True:
                after_balance = await self._safe_get_balance()
                diff = after_balance - before_balance
                last_diff = diff

                logging.info(
                    "Resultado por balance: asset=%s antes=%.2f después=%.2f diff=%.2f",
                    asset,
                    before_balance,
                    after_balance,
                    diff,
                )

                # Threshold mínimo para evitar ruido de fees o timing impreciso
                # WIN puede confirmarse inmediato; LOSS se confirma al final de gracia.
                if diff > 0.01:
                    logging.info("✓ WIN (balance diff=%.2f)", diff)
                    return True
                if diff < -0.01:
                    negative_streak += 1
                    if negative_streak >= early_loss_polls_required:
                        logging.info(
                            "✓ LOSS confirmado temprano (%d lecturas negativas, diff=%.2f)",
                            negative_streak,
                            diff,
                        )
                        return False
                else:
                    negative_streak = 0

                if datetime.now(timezone.utc) >= poll_deadline:
                    if last_diff < -0.01:
                        logging.info(
                            "✓ LOSS confirmado tras ventana de gracia (%ss, diff=%.2f)",
                            grace_seconds,
                            last_diff,
                        )
                        return False
                    logging.warning(
                        "Balance sin cambio significativo (diff=%.2f) — resultado DESCONOCIDO. "
                        "No se ejecutará martingala.",
                        last_diff,
                    )
                    return None

                await asyncio.sleep(0.25)

        except Exception as exc:
            logging.error(
                "No se pudo leer balance para detectar resultado de %s %s: %s",
                asset,
                side,
                exc,
            )
            return None

    async def _safe_get_balance(self) -> float:
        tries = 4
        last_exc: Exception | None = None
        for _ in range(tries):
            try:
                value = await self._pocket_client.get_account_balance()
                self._last_known_balance = value
                return value
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(2)
        raise RuntimeError(f"No se pudo leer balance para monitoreo: {last_exc}")

    async def _get_pre_click_balance_fast(self) -> float:
        """Balance para snapshot pre-click sin bloquear el disparo de orden."""
        try:
            return await asyncio.wait_for(self._safe_get_balance(), timeout=0.9)
        except Exception:
            if self._last_known_balance is not None:
                logging.debug(
                    "Usando balance cacheado pre-click para priorizar timing: %.2f",
                    self._last_known_balance,
                )
                return self._last_known_balance
            return await self._safe_get_balance()

    async def _safe_get_live_price(self, asset: str) -> float | None:
        try:
            return await self._pocket_client.get_live_price(asset, timeout=1.5)
        except Exception as exc:
            logging.debug("No se pudo leer precio vivo de %s: %s", asset, exc)
            return None

    async def _safe_get_live_trade_snapshot(
        self,
        asset: str,
        side: str | None,
    ) -> LiveTradeSnapshot | None:
        try:
            return await self._pocket_client.get_live_trade_snapshot(asset, side, timeout=1.0)
        except Exception as exc:
            logging.debug("No se pudo leer snapshot vivo de Trade para %s %s: %s", asset, side, exc)
            return None

    def _print_waiting_summary(
        self,
        signal: TradingSignal,
        schedule: list[datetime],
        amounts: list[float],
    ) -> None:
        times_label = [self._format_ref_time(item) for item in schedule]
        print_signal_summary(
            asset=signal.asset,
            side=signal.side,
            expiry_minutes=signal.expiry_minutes,
            martingale_mode=self._martingale_mode,
            amounts=amounts,
            schedule_labels=times_label,
            color_output=self._color_output,
        )

    async def _run_countdown_and_prepare(
        self,
        step_name: str,
        signal: TradingSignal,
        execute_at: datetime,
        amount: float,
        prepare_lead_seconds: float,
        send_lead_seconds: float,
        eager_prepare: bool = False,
    ) -> None:
        preparation_done = False
        next_eager_retry_at = datetime.now(timezone.utc)

        if eager_prepare:
            try:
                logging.info(
                    "%s prearmado temprano: preparando orden desde llegada de señal.",
                    step_name,
                )
                await self._pocket_client.prepare_order_for_execution(
                    signal.asset,
                    amount,
                    signal.expiry_minutes,
                    max_retries=3,
                )
                preparation_done = True
            except Exception as exc:
                logging.info(
                    "%s prearmado temprano no completado en primer intento: %s. Se reintentara en countdown.",
                    step_name,
                    exc,
                )
                next_eager_retry_at = datetime.now(timezone.utc) + timedelta(seconds=3)

        while True:
            now_utc = datetime.now(timezone.utc)
            remaining = (execute_at - now_utc).total_seconds()
            if remaining <= send_lead_seconds:
                break

            should_eager_retry = eager_prepare and not preparation_done and now_utc >= next_eager_retry_at
            should_lead_prepare = remaining <= prepare_lead_seconds and not preparation_done

            if should_eager_retry or should_lead_prepare:
                try:
                    await self._pocket_client.prepare_order_for_execution(
                        signal.asset,
                        amount,
                        signal.expiry_minutes,
                        max_retries=3,
                    )
                    preparation_done = True
                except Exception as exc:
                    if should_eager_retry:
                        next_eager_retry_at = datetime.now(timezone.utc) + timedelta(seconds=3)
                    logging.debug("Fallo preparación en countdown %s: %s", step_name, exc)

            remaining_int = max(0, int(remaining))
            mins, sec = divmod(remaining_int, 60)
            hours, mins = divmod(mins, 60)
            if preparation_done:
                semaforo = "VERDE LISTO"
            elif remaining <= prepare_lead_seconds:
                semaforo = "AMARILLO PREPARANDO"
            else:
                semaforo = "ROJO ESPERANDO"
            print_countdown_line(
                step_name=step_name,
                asset=signal.asset,
                side=signal.side,
                amount=amount,
                hh=hours,
                mm=mins,
                ss=sec,
                semaphore=semaforo,
                color_output=self._color_output,
            )
            await asyncio.sleep(0.1)

        clear_countdown_line()

    def _format_ref_time(self, value: datetime) -> str:
        local = value.astimezone(self._reference_tz)
        offset_hours = int(self._reference_tz.utcoffset(None).total_seconds() // 3600)
        return f"{local.strftime('%H:%M:%S')} UTC{offset_hours:+d}"

    def _build_cycle_amounts(self, current_balance: float) -> list[float]:
        if self._martingale_mode == "masaniello" and self._masaniello_session is not None:
            if self._masaniello_manager is not None:
                return self._masaniello_manager_amounts()
            if self._masaniello_session.is_session_blocked:
                logging.warning(
                    "MasanielloSession bloqueada (MAX_LOSSES GUARD). "
                    "Usando amounts cero para skip de senial. "
                    "El bloqueo se libera en el proximo WIN."
                )
                # Devuelve stakes minimos: el engine los ejecutara pero seran $0.01 (dry-safe).
                # Esto preserva el pipeline de timing intacto; el riesgo financiero es nulo.
                return [0.01, 0.01, 0.01]
            return self._masaniello_amounts(current_balance)
        if self._martingale_mode != "calculator":
            return list(self._martingale_amounts)
        return self._calculator_amounts(current_balance)

    def _update_masaniello_manager_after_result(self, result: str) -> None:
        """Actualiza estado de caja negra inmediatamente al cerrar una señal."""
        if self._masaniello_manager is None:
            return
        self._ultimo_resultado = result
        self._next_entry_override = self._masaniello_manager.get_next_stake(self._ultimo_resultado)
        self._ultimo_resultado = None
        self._log_masaniello_next_entry(self._next_entry_override)

    async def check_balance_scaling(self) -> None:
        """Escala la base de sesion Masaniello por hitos de balance.

        Regla:
        - Cada +$50 desde el ancla inicial del proceso, sube +15% la base de sesion.
        - Tope de base: $60.
        """
        if self._masaniello_manager is None:
            return
        try:
            current_balance = await self._safe_get_balance()
        except Exception as exc:
            logging.debug("Scaling Masaniello: no se pudo leer balance (%s)", exc)
            return

        if self._scaling_anchor_balance is None:
            self._scaling_anchor_balance = current_balance
            return

        growth = current_balance - self._scaling_anchor_balance
        if growth < self._balance_scaling_step:
            return

        levels = int(growth // self._balance_scaling_step)
        if self._session_base_increment is not None:
            target_base = self._masaniello_manager.initial_session_base_capital + (
                levels * self._session_base_increment
            )
        else:
            growth_mult = (1.0 + self._session_base_growth_pct) ** levels
            target_base = self._masaniello_manager.initial_session_base_capital * growth_mult
        target_base = min(target_base, self._session_base_max)
        prev_base = self._masaniello_manager.session_base_capital

        if target_base <= prev_base:
            return

        if not self._masaniello_manager.set_session_base_capital(target_base):
            return

        # Fuerza recálculo inmediato para la próxima entrada con la nueva base.
        self._next_entry_override = self._masaniello_manager.get_next_stake(None)
        self._log_masaniello_next_entry(self._next_entry_override)

        growth_msg = (
            f"Nuevo hito alcanzado: Stake de sesion incrementado a ${target_base:.2f} "
            f"(balance=${current_balance:.2f}, base_previa=${prev_base:.2f})"
        )
        logging.info(growth_msg)
        self._emit_event(
            "masaniello_scaling_hito",
            message=growth_msg,
            balance=current_balance,
            growth=growth,
            balance_step=self._balance_scaling_step,
            base_previous=prev_base,
            base_new=target_base,
        )

    def _log_masaniello_next_entry(self, stake: float) -> None:
        if self._masaniello_manager is None:
            return
        snap = self._masaniello_manager.snapshot()
        logging.info(
            "Masaniello -> Siguiente entrada: $%.2f | Estado Sesion: (W: %d, L: %d)",
            stake,
            snap.itms,
            snap.otms,
        )

    def _masaniello_manager_amounts(self) -> list[float]:
        """Calcula [entry, G1, G2] desde MasanielloManager sin tocar timing."""
        assert self._masaniello_manager is not None

        if self._next_entry_override is None:
            self._next_entry_override = self._masaniello_manager.get_next_stake(self._ultimo_resultado)
            self._ultimo_resultado = None
            self._log_masaniello_next_entry(self._next_entry_override)

        entry = max(0.01, round(self._next_entry_override, 2))
        g1_mult = self._recovery_profile.g1_mult
        g2_mult = self._recovery_profile.g2_mult
        amounts = [
            entry,
            round(entry * g1_mult, 2),
            round(entry * g2_mult, 2),
        ]
        return amounts

    def _masaniello_amounts(self, current_balance: float) -> list[float]:
        """Calcula [entry, G1, G2] usando la formula Masaniello para el estado actual de sesion.

        CAP TOTAL: el cap_pct se aplica sobre la exposicion TOTAL de la senial
        (entry + G1 + G2), no sobre cada paso individualmente.

        Con payout=92%: total_mult = 1 + (1.92/0.92) + (1.92/0.92)^2 ≈ 7.44
        entry_max = base * cap_pct / total_mult
        → garantiza que entry + G1 + G2 ≤ base * cap_pct en el peor caso.

        Esto NO afecta timing ni pipeline; solo cambia el tamano de las ordenes.
        """
        assert self._masaniello_session is not None
        entry_raw = self._masaniello_session.current_entry_stake()
        base = self._masaniello_session._base_balance
        g1_mult = self._recovery_profile.g1_mult
        g2_mult = self._recovery_profile.g2_mult
        cap_pct = self._recovery_profile.max_trade_pct

        # Exposicion total si se juegan los 3 pasos: entry * (1 + g1 + g2)
        total_mult = 1.0 + g1_mult + g2_mult
        # entry maxima tal que entry * total_mult == base * cap_pct
        cap_total = round(max(0.01, base * cap_pct), 2)
        entry_max = round(cap_total / total_mult, 4)

        entry = round(min(entry_raw, entry_max), 2)
        entry = max(0.01, entry)

        amounts = [
            entry,
            round(entry * g1_mult, 2),
            round(entry * g2_mult, 2),
        ]
        cap_was_active = entry_raw > entry_max
        logging.info(
            "Masaniello %d/%d: señal %d/%d wins=%d losses=%d "
            "entry_raw=%.2f entry_capped=%.2f cap_total=%.2f(%.0f%%) "
            "total_mult=%.4f g1x=%.4f g2x=%.4f amounts=%s%s",
            self._masaniello_session._n_ops,
            self._masaniello_session._w_needed,
            self._masaniello_session.signals_consumed + 1,
            self._masaniello_session._n_ops,
            self._masaniello_session.wins,
            self._masaniello_session.losses,
            entry_raw,
            entry,
            cap_total,
            cap_pct * 100,
            total_mult,
            g1_mult,
            g2_mult,
            amounts,
            " [CAP TOTAL ACTIVO]" if cap_was_active else "",
        )
        return amounts

    def _calculator_amounts(self, start_balance: float) -> list[float]:
        """Calcula montos del modo calculator usando la lógica de Calculadora-Binarias.

        Reglas aplicadas:
        - Objetivo por escalones: floor(balance) + incremento.
        - Tras cada pérdida simulada, siguiente monto = (objetivo - balance_actual) / payout.
        - Regla 10%: cuando balance > threshold, si el siguiente monto redondeado
          alcanza el límite floor(balance * 0.10), se reinicia el ciclo en ese punto
          recalculando objetivo desde el balance actual.
                - Sin cap extra por paso: el control de riesgo principal es la regla 10%.

        Esta función solo define sizing; no altera el pipeline de timing/scheduling.
        """
        working_balance = max(0.0, float(start_balance))
        target = float(math.floor(working_balance) + self._calc_increment)
        amounts: list[float] = []

        logging.info(
            "Calculator escalonado: start_balance=%.2f target=%.2f payout=%.4f threshold=%.2f steps=%d",
            working_balance,
            target,
            self._calc_payout,
            self._calc_rule10_threshold,
            self._calc_max_steps,
        )

        for step_idx in range(self._calc_max_steps):
            needed = max(0.0, target - working_balance)
            raw_amount = (needed / self._calc_payout) if self._calc_payout > 0 else 0.0
            next_amount = max(0.01, raw_amount)

            if step_idx > 0 and working_balance > self._calc_rule10_threshold:
                limit_10 = math.floor(working_balance * 0.10)
                if limit_10 > 0 and round(next_amount) >= limit_10:
                    prev_target = target
                    target = float(math.floor(working_balance) + self._calc_increment)
                    needed = max(0.0, target - working_balance)
                    raw_amount = (needed / self._calc_payout) if self._calc_payout > 0 else 0.0
                    next_amount = max(0.01, raw_amount)
                    logging.info(
                        "Calculator reset por riesgo (step=%d): next≈%.2f >= limit10=%d | target %.2f → %.2f",
                        step_idx,
                        round(raw_amount, 2),
                        limit_10,
                        prev_target,
                        target,
                    )

            # Ajustar el monto para garantizar incrementos exactos de $2 y redondear a números enteros pares
            amount = round(next_amount, 2)
            amount = max(0.01, amount)
            amounts.append(amount)

            # Simular pérdida del paso para precomputar el siguiente monto de la cadena
            working_balance = max(0.0, working_balance - amount)

            # Ajustar el balance al final del ciclo para que sea un número entero y preferiblemente par
            if step_idx == self._calc_max_steps - 1:
                adjusted_balance = round(working_balance)
                if adjusted_balance % 2 != 0:
                    adjusted_balance += 1  # Asegurar que sea par
                working_balance = adjusted_balance

            logging.info(
                "Calculator step=%d balance=%.2f target=%.2f needed=%.2f raw=%.2f amount=%.2f",
                step_idx,
                working_balance,
                target,
                needed,
                raw_amount,
                amount,
            )

        return amounts

    @staticmethod
    def _is_trade_losing(side: str, entry_price: float | None, live_price: float | None) -> bool:
        if entry_price is None or live_price is None:
            return False
        normalized = (side or "").upper().strip()
        if normalized in {"BUY", "CALL", "UP"}:
            return live_price < entry_price
        return live_price > entry_price

    @staticmethod
    def _is_trade_losing_with_broker(
        side: str,
        entry_price: float | None,
        live_price: float | None,
        broker_snapshot: LiveTradeSnapshot | None,
    ) -> bool:
        if broker_snapshot is not None:
            if broker_snapshot.status == "LOSING":
                return True
            if broker_snapshot.status == "WINNING":
                return False
        return SignalEngine._is_trade_losing(side, entry_price, live_price)

    def _dynamic_timing_leads(self, expiry_minutes: int) -> tuple[float, float]:
        expiry_seconds = max(60.0, float(expiry_minutes) * 60.0)
        send_lead = min(3.0, max(0.4, expiry_seconds * 0.01))
        prepare_lead = min(35.0, max(send_lead + 2.0, expiry_seconds * 0.25))
        return prepare_lead, send_lead

    def _print_realtime_monitor(
        self,
        step_name: str,
        asset: str,
        side: str,
        entry_price: float | None,
        live_price: float | None,
        broker_snapshot: LiveTradeSnapshot | None,
        seconds_to_next: float,
        prepared: bool,
        gate_seconds: float,
        last_print_second: int | None,
    ) -> None:
        return

    @staticmethod
    def _trade_state(side: str, entry_price: float | None, live_price: float | None) -> str:
        if entry_price is None or live_price is None:
            return "GRIS SIN_PRECIO"
        normalized = (side or "").upper().strip()
        if normalized in {"BUY", "CALL", "UP"}:
            return "VERDE GANANDO" if live_price >= entry_price else "ROJO PERDIENDO"
        return "VERDE GANANDO" if live_price <= entry_price else "ROJO PERDIENDO"

    def _paint(self, text: str, code: str) -> str:
        if not self._color_output:
            return text
        return f"{code}{text}\033[0m"

    @staticmethod
    def _color(kind: str) -> str:
        palette = {
            "wait": "\033[96m",
            "info": "\033[37m",
            "countdown": "\033[93m",
            "sent": "\033[94m",
            "win": "\033[92m",
            "loss": "\033[91m",
            "mg": "\033[95m",
        }
        return palette.get(kind, "\033[37m")
