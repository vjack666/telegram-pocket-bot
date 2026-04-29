import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.core.models import TradingSignal
from src.core.console_hub import clear_countdown_line, print_countdown_line, print_order_event, print_signal_summary
from src.pocket_option.assets import canonicalize_pocket_asset, normalize_asset_for_compare
from src.pocket_option.client import PocketOptionBaseClient
from src.pocket_option.trade_panel_feed import LiveTradeSnapshot


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
        event_recorder: Callable[..., None] | None = None,
    ) -> None:
        self._pocket_client = pocket_client
        self._martingale_amounts = [value for value in martingale_amounts if value > 0] or [2.0, 4.0, 10.0]
        self._martingale_mode = martingale_mode if martingale_mode in {"fixed", "calculator"} else "fixed"
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
        self._max_operation_balance_ratio = 0.10
        self._global_gale = global_gale_state
        self._event_recorder = event_recorder
        self._last_known_balance: float | None = None
        # CRITICAL FIX: Broker-level lock to serialize all browser operations
        self._broker_lock = asyncio.Lock()

    async def execute_signal(self, signal: TradingSignal) -> None:
        await self._run_martingale_flow(signal)

    async def _run_martingale_flow(self, signal: TradingSignal) -> None:
        before_cycle_balance = await self._safe_get_balance()
        start_step = 0

        # Toda señal nueva entra por ENTRADA (step=0).
        # Si hay pérdidas acumuladas de señales anteriores, se conservan para el cálculo de montos.
        self._global_gale.reset_for_new_signal(before_cycle_balance)
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
            "Flujo señal: %s %s exp=%sm modo=%s montos=%s start_step=%d",
            signal.asset,
            signal.side,
            signal.expiry_minutes,
            self._martingale_mode,
            cycle_amounts,
            start_step,
        )

        await self._execute_step_chain(signal, cycle_amounts, entry_times, step_idx=start_step)

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
                self._global_gale.record_win()
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
            else:
                logging.warning("Resultado: LOSS en %s. Gale continuará en siguiente señal.", step_name)
                self._global_gale.record_loss(amount)
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
        max_entry_delay = self._max_entry_delay_seconds
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
            self._global_gale.record_win()
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
        # Registrar LOSS en el estado global antes de continuar al siguiente paso
        self._global_gale.record_loss(current_amount)
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

    async def _click_prepared_step_immediate(
        self,
        step_name: str,
        signal: TradingSignal,
        entry_at: datetime,
        amount: float,
    ) -> tuple[float, float | None, datetime]:
        """Ejecuta un paso YA prearmado sin re-preparar ni revalidar activo."""
        before_balance = await self._get_pre_click_balance_fast()
        entry_price = None

        logging.info(
            "%s CLICK INMEDIATO (prearmada): %s %s amount=%.2f entry_price=%s",
            step_name,
            signal.asset,
            signal.side,
            amount,
            f"{entry_price:.5f}" if entry_price is not None else "n/d",
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
        try:
            poll_deadline = datetime.now(timezone.utc) + timedelta(seconds=max(1, self._result_grace_seconds))
            while True:
                after_balance = await self._safe_get_balance()
                diff = after_balance - before_balance

                logging.info(
                    "Resultado por balance: asset=%s antes=%.2f después=%.2f diff=%.2f",
                    asset,
                    before_balance,
                    after_balance,
                    diff,
                )

                # Threshold mínimo para evitar ruido de fees o timing impreciso
                if diff < -0.01:
                    logging.info("✓ LOSS (balance diff=%.2f)", diff)
                    return False
                if diff > 0.01:
                    logging.info("✓ WIN (balance diff=%.2f)", diff)
                    return True

                if datetime.now(timezone.utc) >= poll_deadline:
                    logging.warning(
                        "Balance sin cambio significativo (diff=%.2f) — resultado DESCONOCIDO. "
                        "No se ejecutará martingala.",
                        diff,
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
        side: str,
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
        if self._martingale_mode != "calculator":
            return list(self._martingale_amounts)
        return self._calculator_amounts(current_balance)

    def _calculator_amounts(self, start_balance: float) -> list[float]:
        # Con gale global, siempre calculamos desde el balance de inicio del ciclo
        # y con target fijo de +$2 desde ese punto, sin importar pérdidas acumuladas
        if self._global_gale.is_active:
            cycle_start = self._global_gale.cycle_start_balance
            accumulated_loss = self._global_gale.accumulated_loss
            balance = max(0.0, cycle_start - accumulated_loss)
            target = cycle_start + 2.0  # Target fijo de +$2 desde inicio del ciclo
            logging.info(
                "Calculator modo gale global: cycle_start=%.2f accumulated_loss=%.2f balance_actual=%.2f target=%.2f",
                cycle_start,
                accumulated_loss,
                balance,
                target,
            )
        else:
            balance = max(0.0, start_balance)
            target = float(balance.__floor__() + self._calc_increment)
            logging.info(
                "Calculator modo normal: balance=%.2f target=%.2f",
                balance,
                target,
            )
        
        losses = 0
        amounts: list[float] = []
        # Tope fijo por señal: no gastar más del 10% de la cuenta por operación.
        risk_cap = round(max(0.01, start_balance * self._max_operation_balance_ratio), 2)
        cap_reached = False

        for _ in range(self._calc_max_steps):
            if cap_reached:
                amount = risk_cap
            else:
                needed_profit = max(0.0, target - balance)
                amount = needed_profit / self._calc_payout
                if amount <= 0:
                    amount = 0.01
                amount = round(max(0.01, amount), 2)

                if amount > risk_cap:
                    logging.info(
                        "Regla 10%% aplicada: monto_calculado=%.2f tope_fijo=%.2f. Se mantiene monto plano.",
                        amount,
                        risk_cap,
                    )
                    amount = risk_cap
                    cap_reached = True

            amounts.append(amount)

            balance = max(0.0, balance - amount)
            losses += 1

            # Reglas especiales solo en modo normal (no en gale global)
            if not self._global_gale.is_active:
                if balance <= self._calc_rule10_threshold and losses >= 3:
                    losses = 0
                    target = float(balance.__floor__() + self._calc_increment)
                    continue

                if balance > self._calc_rule10_threshold:
                    next_needed = max(0.0, target - balance)
                    next_amount = next_needed / self._calc_payout if self._calc_payout > 0 else 0.0
                    risk_limit = float(int(balance * 0.10))
                    if round(next_amount) >= risk_limit and risk_limit > 0:
                        losses = 0
                        target = float(balance.__floor__() + self._calc_increment)

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
