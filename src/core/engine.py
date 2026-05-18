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
from src.core.session_manager import SessionManager

from src.core.equity_bands import EquityBandManager
from src.core.daily_profit_tracker import DailyProfitTracker
from src.utils.session_learning_db import SessionLearningDB
from src.core.manual_operation_tracker import ManualOperationTracker
from src.utils.payout_guard import check_payout_or_notify


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
        session_manager: SessionManager | None = None,
        recovery_profile = None,
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
        pocket_min_order_amount: float = 1.0,
        session_loss_brake_enabled: bool = True,
        session_loss_brake_window_minutes: int = 180,
        session_loss_brake_step: float = 0.25,
        session_loss_brake_floor: float = 0.25,
        payout_min_profitable: float = 0.85,
    ) -> None:
        self._pocket_client = pocket_client
        self._martingale_amounts = [value for value in martingale_amounts if value > 0] or [2.0, 4.0, 10.0]
        self._martingale_mode = "session"
        self._session_manager = session_manager
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
        # RecoveryProfile eliminado temporalmente
        self._recovery_profile = None
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
        # Se mantiene G2 disponible en el flujo; la aprobación humana puede deshabilitarse por config.
        _ = g2_human_approval
        _ = g2_approval_timeout_seconds
        self._g2_human_approval = False
        self._g2_approval_timeout_seconds = 0
        self._session_learning_db = session_learning_db
        self._operation_mode_schedule_enabled = bool(operation_mode_schedule_enabled)
        self._operation_mode_hybrid_start_hour = max(0, min(23, int(operation_mode_hybrid_start_hour)))
        self._operation_mode_hybrid_end_hour = max(0, min(24, int(operation_mode_hybrid_end_hour)))
        self._operation_mode_sound_alert = bool(operation_mode_sound_alert)
        self._operation_mode_last: str | None = None
        # Estado del ciclo actual para aprendizaje
        self._cycle_sequence: str = ""         # "W", "L", "WL", "LL", etc.
        # CRITICAL FIX: Broker-level lock to serialize all browser operations
        self._broker_lock = asyncio.Lock()
        
        logging.info("[MODO OPERATIVO] Money management opera con ENTRADA + G1 + G2")

        # Manual Operation Tracker — para registrar operaciones manuales del usuario
        self._manual_operation_tracker = manual_operation_tracker
        # Flag: el bot está en medio de una operación propia (para ignorar ese cambio de saldo)
        self._bot_trade_in_progress: bool = False
        # Si una operación manual gana antes del click programado, cancelar la señal activa.
        self._cancel_active_signal_by_manual_win: bool = False
        self._pocket_min_order_amount = max(0.01, float(pocket_min_order_amount))
        # Freno automático por racha de pérdidas recientes (ventana deslizante)
        self._session_loss_brake_enabled = bool(session_loss_brake_enabled)
        self._session_loss_brake_window = timedelta(
            minutes=max(1, int(session_loss_brake_window_minutes))
        )
        self._session_loss_brake_step = max(0.0, float(session_loss_brake_step))
        self._session_loss_brake_floor = max(0.05, min(1.0, float(session_loss_brake_floor)))
        self._recent_cycle_loss_times: list[datetime] = []
        self._payout_min_profitable: float = max(0.01, min(0.99, float(payout_min_profitable)))

    async def start_asset_payout_monitor(
        self,
        poll_interval: float = 8.0,
    ) -> None:
        """Loop en background que detecta cambios de par seleccionado en el broker.

        Cada `poll_interval` segundos lee el activo activo en la UI. Si cambió:
        - Refresca el payout del nuevo par.
        - Si el nuevo payout cae por debajo de `_payout_min_profitable`, emite
          advertencia en log y muestra el popup (modo HÍBRIDO) o sólo loguea (AUTOMATICO).
          La advertencia NO bloquea señales ya en curso; actúa sobre las siguientes.
        """
        logging.info(
            "[AssetPayoutMonitor] Iniciado — poll=%.1fs | umbral_rentabilidad=%.0f%%",
            poll_interval,
            self._payout_min_profitable * 100.0,
        )
        last_asset: str | None = None

        while True:
            try:
                await asyncio.sleep(poll_interval)
                current_asset = await self._pocket_client.get_selected_asset()
                if not current_asset:
                    continue

                if last_asset is None:
                    last_asset = current_asset
                    continue

                if current_asset == last_asset:
                    continue

                # ── Cambio detectado ──────────────────────────────────────────
                prev_asset = last_asset  # guardar antes de actualizar
                last_asset = current_asset
                logging.info(
                    "[AssetPayoutMonitor] Cambio de par detectado: %s → %s. Refrescando payout y saldo.",
                    prev_asset,
                    current_asset,
                )
                self._emit_event(
                    "asset_changed_by_user",
                    previous_asset=prev_asset,
                    new_asset=current_asset,
                )

                # Refrescar payout del nuevo par
                await self._refresh_dynamic_payout(current_asset)

                # Refrescar saldo para que la calculadora tenga datos actualizados
                # del nuevo par antes de recibir la próxima señal
                try:
                    fresh_balance = await self._safe_get_balance()
                    logging.info(
                        "[AssetPayoutMonitor] Saldo actualizado tras cambio de par: $%.2f",
                        fresh_balance,
                    )
                    if self._session_manager is not None:
                        self._session_manager.observe_balance(fresh_balance)
                except Exception as _bal_exc:
                    logging.debug(
                        "[AssetPayoutMonitor] No se pudo releer saldo tras cambio de par: %s",
                        _bal_exc,
                    )

                # Comprobar rentabilidad del nuevo par
                payout_pct = self._calc_payout * 100.0
                if payout_pct / 100.0 < self._payout_min_profitable:
                    operation_mode = self._operation_mode_last or "AUTOMATICO"
                    is_manual = operation_mode == "HIBRIDO"
                    logging.warning(
                        "[AssetPayoutMonitor] ⛔ Par %s tiene payout NO rentable: %.1f%% < %.0f%%",
                        current_asset,
                        payout_pct,
                        self._payout_min_profitable * 100.0,
                    )
                    self._emit_event(
                        "asset_payout_unprofitable_on_change",
                        asset=current_asset,
                        payout_percent=payout_pct,
                        min_profitable_pct=self._payout_min_profitable * 100.0,
                        operation_mode=operation_mode,
                    )
                    if is_manual:
                        await check_payout_or_notify(
                            asset=current_asset,
                            payout_percent=payout_pct,
                            min_profitable=self._payout_min_profitable,
                            is_manual_mode=True,
                        )
                else:
                    logging.info(
                        "[AssetPayoutMonitor] Par %s — payout %.1f%% ✓ (mínimo=%.0f%%)",
                        current_asset,
                        payout_pct,
                        self._payout_min_profitable * 100.0,
                    )

            except asyncio.CancelledError:
                logging.info("[AssetPayoutMonitor] Monitor detenido.")
                return
            except Exception as exc:
                logging.debug("[AssetPayoutMonitor] Error en ciclo de polling: %s", exc)

    async def start_balance_monitor(
        self,
        poll_interval: float = 2.5,
        min_change: float = 0.5,
    ) -> None:
        """Loop en background que detecta operaciones manuales por cambio de saldo.

        Cada `poll_interval` segundos lee el saldo. Si detecta un cambio >= `min_change`
        que NO proviene de una operación del bot (flag _bot_trade_in_progress=False),
        lo trata como operación manual: determina WIN/LOSS y actualiza sesión.
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
                            asset=pending_manual.get("asset_hint"),
                            side=pending_manual.get("side_hint"),
                            amount=pending_manual.get("amount_hint"),
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
                    asset=None,
                    side=None,
                    amount=None,
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

    def _prune_recent_cycle_losses(self, now_utc: datetime) -> None:
        cutoff = now_utc - self._session_loss_brake_window
        self._recent_cycle_loss_times = [
            ts for ts in self._recent_cycle_loss_times if ts >= cutoff
        ]

    def _register_cycle_loss(self, now_utc: datetime | None = None) -> None:
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        self._recent_cycle_loss_times.append(now_utc)
        self._prune_recent_cycle_losses(now_utc)

    def _loss_brake_multiplier(self, now_utc: datetime | None = None) -> float:
        if not self._session_loss_brake_enabled:
            return 1.0
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        self._prune_recent_cycle_losses(now_utc)
        losses_recent = len(self._recent_cycle_loss_times)
        multiplier = 1.0 - (losses_recent * self._session_loss_brake_step)
        return max(self._session_loss_brake_floor, min(1.0, multiplier))

    def _apply_broker_amount_floor(self, amount: float) -> float:
        adjusted = max(self._pocket_min_order_amount, float(amount))
        return math.ceil(adjusted * 100.0) / 100.0

    async def _inject_next_stake_post_result(self) -> None:
        """Escribe en la caja del broker el próximo stake según Masaniello,
        inmediatamente tras conocer el resultado (WIN o LOSS).
        Garantiza que la caja siempre quede lista para la siguiente señal."""
        if self._session_manager is None:
            return
        try:
            next_amount = self._apply_broker_amount_floor(
                self._session_manager.get_stakes_para_senal(self._pocket_min_order_amount)["entry"]
            )
            await self._pocket_client.set_amount(next_amount, max_retries=2)
            logging.info(
                "[PostResult] ✅ Próximo stake inyectado en broker: $%.2f",
                next_amount,
            )
        except Exception as exc:
            logging.warning(
                "[PostResult] ⚠️  No se pudo escribir próximo stake en broker: %s", exc
            )

    def _sync_session_loss_from_global(self) -> None:
        if self._session_manager is None:
            return
        if hasattr(self._session_manager, "sync_accumulated_loss"):
            try:
                self._session_manager.sync_accumulated_loss(self._global_gale.accumulated_loss)
            except Exception:
                pass

    @staticmethod
    def _session_result_label(step_name: str, won: bool) -> str:
        if not won:
            return "LOSS"
        if step_name == "ENTRADA":
            return "WIN DIRECTO"
        if step_name == "MARTINGALA 1":
            return "G1"
        return "G2"

    def _alert_session_stop_loss(self) -> None:
        message = "Sesión Finalizada por Stop Loss - Capital Protegido"
        clear_countdown_line()
        logging.error(message)
        print(f"\n[ALERTA] {message}")
        self._emit_event(
            "session_stop_loss_triggered",
            message=message,
            blocks_lost_today=self._session_manager.blocks_lost_today if self._session_manager else None,
        )

    def _update_session_after_result(self, step_name: str, won: bool) -> None:
        if self._session_manager is None:
            return

        normalized_step = str(step_name).strip().upper()
        if normalized_step == "ENTRADA":
            status = self._session_manager.registrar_resultado_senal(
                "WIN" if won else "LOSS",
                None if won else "LOSS",
            )
        elif normalized_step == "MARTINGALA 1":
            status = self._session_manager.registrar_resultado_senal(
                "LOSS",
                "WIN" if won else "LOSS",
            )
        else:
            # G2 ya no forma parte del money management de sesión.
            return

        if status.get("sesion_pausada"):
            self._alert_session_stop_loss()

    async def _prefill_manual_loss_amount(self, pending_manual: dict[str, Any]) -> None:
        """Prefill cómodo: escribir monto provisional asumiendo LOSS manual, sin mutar estado real."""
        if self._session_manager is None:
            return
        if pending_manual.get("prefill_loss_written"):
            return

        # Sincronizar payout dinámico del activo antes de calcular el prefill.
        try:
            asset_hint = str(pending_manual.get("asset_hint") or "").strip()
            if asset_hint:
                await self._refresh_dynamic_payout(asset_hint)
        except Exception as exc:
            logging.debug("[BalanceMonitor] No se pudo refrescar payout para prefill manual: %s", exc)

        try:
            next_if_loss = float(
                self._session_manager.peek_next_stake_if_loss(
                    min_order=self._pocket_min_order_amount
                )
            )
        except Exception as exc:
            logging.debug("[BalanceMonitor] No se pudo previsualizar stake post-loss manual: %s", exc)
            return

        adjusted_next_if_loss = self._apply_broker_amount_floor(next_if_loss)

        if adjusted_next_if_loss <= 0:
            return

        try:
            await self._pocket_client.set_amount(adjusted_next_if_loss, max_retries=2)
            pending_manual["prefill_loss_written"] = True
            pending_manual["prefill_loss_amount"] = adjusted_next_if_loss
            logging.info(
                "[BalanceMonitor] Prefill manual (si pierde) escrito: $%.2f",
                adjusted_next_if_loss,
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
        asset: str | None = None,
        side: str | None = None,
        amount: float | None = None,
    ) -> None:
        """
        Reconciliación total de operación manual.
        
        Este método es el PUNTO CENTRAL donde una intervención manual se integra
        completamente en el estado global del bot. Actualiza:
        - GlobalGaleState (accumulated_loss, current_step, target_balance)
        - SessionManager (wins/losses para conteo de sesión)
        - ManualOperationTracker (historial auditable)
        - Gestor de sesión (próximo stake)
        
        Args:
            result: "W" (WIN) o "L" (LOSS)
            balance_before: Saldo antes de la operación
            balance_after: Saldo después de la operación
            asset: Activo (ej: "EURUSD OTC") - extraído de snapshot si es posible
            side: "BUY" o "SELL" - extraído de snapshot si es posible
            amount: Monto arriesgado - extraído de snapshot si es posible
        """
        result_label = "WIN" if result == "W" else "LOSS"
        emoji = "✅ GANADA" if result == "W" else "❌ PERDIDA"
        diff = balance_after - balance_before
        observed_payout: float | None = None
        
        # Estimar monto si no fue pasado
        if amount is None:
            amount = abs(balance_before - balance_after) if diff < 0 else abs(diff)
        if result == "W" and amount and amount > 0 and diff > 0:
            observed_payout = float(diff) / float(amount)
        if asset is None:
            asset = "EURUSD OTC"  # fallback default
        if side is None:
            side = "BUY"  # fallback default
        
        logging.info(
            "[BalanceMonitor] ⏸️  CIERRE DETECTADO - Operación manual %s | "
            "%s %s $%.2f | cambio=$%.2f (saldo: %.2f → %.2f)",
            emoji,
            side,
            asset,
            amount,
            diff,
            balance_before,
            balance_after,
        )

        # Si una manual WIN cierra antes de que el bot cliquee su señal programada,
        # anular esa señal evita doble exposición en la misma ventana.
        if result == "W" and not self._bot_trade_in_progress:
            self._cancel_active_signal_by_manual_win = True
            logging.info(
                "[BalanceMonitor] WIN manual confirmado. Se anulará la señal programada pendiente.",
            )
            self._emit_event(
                "manual_win_override_signal",
                balance_before=balance_before,
                balance_after=balance_after,
                diff=diff,
                asset=asset,
                side=side,
                amount=amount,
            )
        
        # ─────────────────────────────────────────────────────────────────────
        # PASO 1: SINCRONIZACIÓN CON GLOBAL GALE STATE
        # ─────────────────────────────────────────────────────────────────────
        # Actualizar el estado de riesgo global inmediatamente.
        # El bot SIENTE esta operación en su gestión de martingala.
        
        if result == "W":
            self._global_gale.record_win()
        else:  # result == "L"
            self._global_gale.record_loss(amount)
            self._register_cycle_loss()
        self._sync_session_loss_from_global()
        
        # ─────────────────────────────────────────────────────────────────────
        # PASO 2: SINCRONIZACIÓN CON SESSION MANAGER (saldo real reconciliado)
        # ─────────────────────────────────────────────────────────────────────
        if self._session_manager is not None:
            status = self._session_manager.registrar_resultado_externo(
                "WIN" if result == "W" else "LOSS",
                balance_before=balance_before,
                balance_after=balance_after,
                observed_payout=observed_payout,
            )
            if status.get("sesion_pausada"):
                self._alert_session_stop_loss()
        
        # ─────────────────────────────────────────────────────────────────────
        # PASO 3: REGISTRO EN MANUAL OPERATION TRACKER
        # ─────────────────────────────────────────────────────────────────────
        # Crear registro auditable formal de la intervención manual
        
        if self._manual_operation_tracker is not None:
            op = self._manual_operation_tracker.register_manual_operation(
                asset=asset,
                side=side,
                amount=amount,
                balance_before=balance_before,
                result=result_label,
                balance_after=balance_after,
                notes="Detección automática por monitor de balance",
                apply_state=False,
            )
            op_timestamp = op.timestamp.isoformat() if op.timestamp is not None else "unknown"
            logging.info(
                "[BalanceMonitor] Manual registrada en tracker: id=%s",
                op_timestamp,
            )
        
        # ─────────────────────────────────────────────────────────────────────
        # PASO 4: PREFILL DEL SIGUIENTE STAKE DESDE SESSION MANAGER
        # ─────────────────────────────────────────────────────────────────────
        if self._session_manager is not None:
            try:
                # Refrescar payout del activo reconciliado para calcular stake con dato vigente.
                asset_for_payout = str(asset or "").strip()
                if asset_for_payout:
                    await self._refresh_dynamic_payout(asset_for_payout)

                next_amount = self._session_manager.get_stakes_para_senal(self._pocket_min_order_amount)["entry"]
                await self._pocket_client.set_amount(next_amount, max_retries=2)
                logging.info(
                    "[BalanceMonitor] 💰 Siguiente monto SessionManager escrito: $%.2f "
                    "(min_broker=%.2f)",
                    next_amount,
                    self._pocket_min_order_amount,
                )
            except Exception as exc:
                logging.warning(
                    "[BalanceMonitor] ⚠️  No se pudo escribir monto en broker: %s", exc
                )
        
        # ─────────────────────────────────────────────────────────────────────
        # PASO 5: EVENTO AGREGADO PARA LISTENERS EXTERNOS
        # ─────────────────────────────────────────────────────────────────────
        
        self._emit_event(
            "manual_trade_reconciled",
            result=result,
            balance_before=balance_before,
            balance_after=balance_after,
            diff=diff,
            asset=asset,
            side=side,
            amount=amount,
            global_gale_state={
                "is_active": self._global_gale.is_active,
                "current_step": self._global_gale.current_step,
                "accumulated_loss": self._global_gale.accumulated_loss,
                "cycle_start_balance": self._global_gale.cycle_start_balance,
                "target_balance": self._global_gale.target_balance,
            },
            session_manager={
                "wins": self._session_manager.wins if self._session_manager else None,
                "losses": self._session_manager.losses if self._session_manager else None,
                "signals_consumed": self._session_manager.signals_consumed if self._session_manager else None,
                "is_session_blocked": self._session_manager.sesion_pausada if self._session_manager else None,
            } if self._session_manager else None,
        )
        
        logging.info(
            "[BalanceMonitor] Reconciliación manual aplicada | "
            "Global(step=%d loss=%.2f target=%.2f) Session(%d/%d)",
            self._global_gale.current_step,
            self._global_gale.accumulated_loss,
            self._global_gale.target_balance,
            self._session_manager.wins if self._session_manager else 0,
            self._session_manager.losses if self._session_manager else 0,
        )

    async def execute_signal(self, signal: TradingSignal) -> None:
        self._cancel_active_signal_by_manual_win = False
        try:
            await self._run_martingale_flow(signal)
        finally:
            self._bot_trade_in_progress = False
            self._cancel_active_signal_by_manual_win = False

    async def _run_martingale_flow(self, signal: TradingSignal) -> None:
        self._cancel_active_signal_by_manual_win = False
        if self._session_manager is not None and self._session_manager.sesion_pausada:
            self._alert_session_stop_loss()
            self._emit_event(
                "global_stop_active",
                asset=signal.asset,
                side=signal.side,
                blocks_lost_today=self._session_manager.blocks_lost_today,
            )
            return

        before_cycle_balance = await self._safe_get_balance()
        start_step = 0
        operation_mode = self._refresh_operation_mode(signal)
        await self._refresh_dynamic_payout(signal.asset)

        # ── Comprobación de rentabilidad del payout ──────────────────────────
        _payout_pct = self._calc_payout * 100.0
        _is_manual = operation_mode == "HIBRIDO"
        _payout_ok = await check_payout_or_notify(
            asset=signal.asset or "N/A",
            payout_percent=_payout_pct,
            min_profitable=self._payout_min_profitable,
            is_manual_mode=_is_manual,
        )
        if not _payout_ok:
            self._emit_event(
                "signal_skipped_low_payout",
                asset=signal.asset,
                side=signal.side,
                payout_percent=_payout_pct,
                min_profitable_pct=self._payout_min_profitable * 100.0,
                operation_mode=operation_mode,
            )
            return
        # ─────────────────────────────────────────────────────────────────────

        # Fuente unica de verdad para heredar deuda: GlobalGaleState.
        should_inherit_manual_loss = self._global_gale.accumulated_loss > 0
        
        self._global_gale.reset_for_new_signal(
            before_cycle_balance, 
            inherit_manual_loss=should_inherit_manual_loss
        )
        self._sync_session_loss_from_global()
        
        # Resetear estado de aprendizaje del ciclo
        self._cycle_sequence = ""
        logging.info(
            "Nueva señal inicia en ENTRADA (step=0) | balance=%.2f accumulated_loss=%.2f target=%.2f | "
            "inherit_manual_loss=%s",
            before_cycle_balance,
            self._global_gale.accumulated_loss,
            self._global_gale.target_balance,
            should_inherit_manual_loss,
        )
        
        cycle_amounts = self._build_cycle_amounts(before_cycle_balance)
        if not cycle_amounts:
            logging.warning(
                "Señal omitida: SessionManager indicó sesión pausada o sin montos válidos."
            )
            self._emit_event(
                "signal_skipped_session_paused",
                asset=signal.asset,
                side=signal.side,
            )
            return
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
        label = self._session_label_from_seq(seq)
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
            session_sequence=seq,
            session_label=label,
            step_reached=step_reached,
            g2_intervened=False,
            g2_approved=None,
            g2_amount=None,
            session_pnl=session_pnl,
            won=won,
            expiry_minutes=signal.expiry_minutes,
        )

    @staticmethod
    def _session_label_from_seq(seq: str) -> str:
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

        Si la banda cambia, sincroniza _calc_base_balance para el próximo ciclo.
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
        self._bot_trade_in_progress = True
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
            try:
                won = await self._monitor_order_result_until_close(
                    before_balance,
                    close_at,
                    signal.asset,
                    signal.side,
                    entry_delay=entry_delay,
                )
            finally:
                self._bot_trade_in_progress = False
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
                self._update_session_after_result(step_name, won=True)
                
                # Registrar en daily profit tracker
                pnl_win = round(amount * self._calc_payout, 2)
                if self._daily_profit_tracker is not None:
                    tracker_status = self._daily_profit_tracker.record_trade(pnl_win)
                    self._emit_event("daily_profit_update", **tracker_status)
                    if tracker_status.get("meta_just_reached"):
                        logging.info("[Daily Meta] ✓ META ALCANZADA: $%.2f | Modo DEFENSIVO activado", 
                                   tracker_status.get("daily_pnl", 0))
                        if self._session_manager is not None:
                            self._session_manager.reset_session(reason="daily_target_reached", clear_stop_flags=True)
                
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
                # Inyectar próximo stake en broker inmediatamente tras WIN
                await self._inject_next_stake_post_result()
                # Watchdog: escaneo post-trade con delay de 5s
                if self._watchdog_trigger is not None:
                    self._watchdog_trigger()
            else:
                logging.warning("Resultado: LOSS en %s. Gale continuará en siguiente señal.", step_name)
                self._cycle_sequence += "L"  # tracking de aprendizaje
                self._global_gale.record_loss(amount)
                self._register_cycle_loss()
                self._update_session_after_result(step_name, won=False)
                
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
                    if tracker_status.get("meta_just_reached") and self._session_manager is not None:
                        self._session_manager.reset_session(reason="daily_target_reached", clear_stop_flags=True)
                
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
                # Inyectar próximo stake en broker inmediatamente tras LOSS final
                await self._inject_next_stake_post_result()
                # Watchdog: escaneo post-trade con delay de 5s
                if self._watchdog_trigger is not None:
                    self._watchdog_trigger()
            return

        try:
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
        except Exception:
            self._bot_trade_in_progress = False
            raise
        if next_info is None:
            self._bot_trade_in_progress = False
            return

        # ── Chequeo payout mid-gale (antes de ejecutar siguiente paso) ────────
        await self._refresh_dynamic_payout(signal.asset)
        _payout_pct_mg = self._calc_payout * 100.0
        _is_manual_mg = (self._operation_mode_last or "AUTOMATICO") == "HIBRIDO"
        _payout_ok_mg = await check_payout_or_notify(
            asset=signal.asset or "N/A",
            payout_percent=_payout_pct_mg,
            min_profitable=self._payout_min_profitable,
            is_manual_mode=_is_manual_mg,
        )
        if not _payout_ok_mg:
            logging.warning(
                "⛔ Martingala %d cancelada por payout insuficiente mid-sesión: %.1f%% < %.0f%%",
                step_idx + 1,
                _payout_pct_mg,
                self._payout_min_profitable * 100.0,
            )
            self._emit_event(
                "gale_step_skipped_low_payout",
                asset=signal.asset,
                side=signal.side,
                step_idx=step_idx + 1,
                payout_percent=_payout_pct_mg,
                min_profitable_pct=self._payout_min_profitable * 100.0,
            )
            return
        # ─────────────────────────────────────────────────────────────────────

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
            self._update_session_after_result(current_step_name, won=True)
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
            # Inyectar próximo stake en broker inmediatamente tras WIN intermedio
            await self._inject_next_stake_post_result()
            return None

        logging.info("%s cerro en LOSS. Continuando con %s.", current_step_name, next_step_name)
        self._cycle_sequence += "L"  # tracking de aprendizaje
        # Registrar LOSS en el estado global antes de continuar al siguiente paso
        # Nota: NO se registra en SessionManager aquí — es un paso intermedio.
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
        if self._cancel_active_signal_by_manual_win:
            logging.info(
                "%s cancelada: WIN manual detectado antes del click programado.",
                step_name,
            )
            return False

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
                if "MANUAL_WIN_OVERRIDE" in str(exc):
                    logging.info(
                        "%s cancelada por override de WIN manual durante countdown.",
                        step_name,
                    )
                    return False
                # CRITICAL FIX: If asset not available, cancel signal immediately
                if "ACTIVO_NO_DISPONIBLE" in str(exc):
                    logging.warning(
                        "%s cancelada: activo %s no disponible en broker",
                        step_name,
                        signal.asset,
                    )
                    raise RuntimeError(f"{step_name} cancelada: activo no disponible")
                raise
            if self._cancel_active_signal_by_manual_win:
                logging.info(
                    "%s cancelada: WIN manual confirmado justo antes del click.",
                    step_name,
                )
                return False
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

        if self._cancel_active_signal_by_manual_win:
            logging.info(
                "%s cancelada en ventana inmediata por WIN manual confirmado.",
                step_name,
            )
            return False

        # Señal en ventana inmediata: puede no haber pasado por _run_countdown_and_prepare.
        # Forzamos preparación rápida para no clickear con el activo/monto anterior.
        from src.core.console_hub import print_countdown_line, clear_countdown_line
        if self._pocket_client.execute_orders_enabled():
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
                if self._session_manager is not None and hasattr(self._session_manager, "observe_balance"):
                    try:
                        self._session_manager.observe_balance(value)
                    except Exception:
                        pass
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

    async def _refresh_dynamic_payout(self, asset: str | None) -> None:
        """Intenta sincronizar payout real del broker para cálculo de montos/PnL."""
        try:
            payout_percent = await self._pocket_client.get_current_payout_percent(asset)
        except Exception as exc:
            logging.debug("No se pudo leer payout dinámico (%s): %s", asset, exc)
            return

        if payout_percent is None:
            return

        payout_net = max(0.01, float(payout_percent) / 100.0)
        if abs(payout_net - self._calc_payout) < 1e-9:
            return

        old_percent = self._calc_payout * 100.0
        self._calc_payout = payout_net
        logging.info(
            "Payout dinámico sincronizado: %.2f%% -> %.2f%% (asset=%s)",
            old_percent,
            payout_percent,
            asset or "N/A",
        )
        if self._session_manager is not None:
            self._session_manager.update_payout(payout_net)

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
        show_terminal_countdown = self._pocket_client.execute_orders_enabled()

        if eager_prepare:
            if self._cancel_active_signal_by_manual_win:
                raise RuntimeError("MANUAL_WIN_OVERRIDE")
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
            if self._cancel_active_signal_by_manual_win:
                raise RuntimeError("MANUAL_WIN_OVERRIDE")
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
            if show_terminal_countdown:
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

        if show_terminal_countdown:
            clear_countdown_line()

    def _format_ref_time(self, value: datetime) -> str:
        local = value.astimezone(self._reference_tz)
        offset_hours = int(self._reference_tz.utcoffset(None).total_seconds() // 3600)
        return f"{local.strftime('%H:%M:%S')} UTC{offset_hours:+d}"

    def _build_cycle_amounts(self, current_balance: float) -> list[float]:
        if self._session_manager is not None:
            self._sync_session_loss_from_global()
            if self._session_manager.sesion_pausada:
                logging.warning(
                    "SessionManager pausado por stop-loss de sesión. "
                    "No se construirá plan de montos para esta señal."
                )
                return []
            return self._session_amounts()
        return list(self._martingale_amounts)

    async def check_balance_scaling(self) -> None:
        """Deshabilitado: modo bloque fijo ignora crecimiento de saldo del broker."""
        return

    def _session_amounts(self) -> list[float]:
        """Calcula [entry, G1, G2] exclusivamente desde SessionManager."""
        assert self._session_manager is not None

        stakes = self._session_manager.get_stakes_para_senal(self._pocket_min_order_amount)
        entry = self._apply_broker_amount_floor(stakes["entry"])
        raw_g1 = float(stakes.get("g1", 0.0) or 0.0)
        raw_g2 = float(
            self._session_manager.peek_stake_after_losses(
                losses_ahead=2,
                min_order=self._pocket_min_order_amount,
            )
            or 0.0
        )
        amounts = [entry]
        g1_amount = 0.0
        g2_amount = 0.0
        if raw_g1 > 0:
            g1_amount = self._apply_broker_amount_floor(raw_g1)
            amounts.append(g1_amount)
        if raw_g2 > 0:
            g2_amount = self._apply_broker_amount_floor(raw_g2)
            amounts.append(g2_amount)
        logging.info(
            "Sesion %d/%d: señal %d/%d wins=%d losses=%d "
            "entry=%.2f g1=%.2f g2=%.2f max_riesgo=%.2f amounts=%s",
            self._session_manager.n_ops,
            self._session_manager.w_needed,
            self._session_manager.signals_consumed + 1,
            self._session_manager.n_ops,
            self._session_manager.wins,
            self._session_manager.losses,
            entry,
            g1_amount,
            g2_amount,
            float(stakes["max_riesgo"]),
            amounts,
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
        # Ventana más conservadora para reducir desincronización en expiraciones cortas.
        send_lead = min(3.0, max(1.2, expiry_seconds * 0.015))
        prepare_lead = min(40.0, max(send_lead + 3.0, expiry_seconds * 0.30))
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

