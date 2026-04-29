import asyncio
import logging
import os
import signal
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from types import FrameType
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from dotenv import load_dotenv

from src.config.settings import AppSettings
from src.core.engine import SignalEngine
from src.core.models import TradingSignal
from src.core.pipeline import MessageQueue, SignalProcessor, StateManager, GlobalGaleState
from src.pocket_option.client import PocketOptionDemoClient
from src.utils.blackbox import DeferredBlackBoxRecorder, ShutdownSnapshot
from src.utils.logger import setup_logging


@dataclass
class ShutdownDiagnostics:
    reason: str = "normal_exit"
    last_exception: str = ""
    component: str = "main"
    signal_name: str = ""


_shutdown_diag = ShutdownDiagnostics()


def _build_shutdown_snapshot() -> ShutdownSnapshot:
    return ShutdownSnapshot(
        reason=_shutdown_diag.reason,
        component=_shutdown_diag.component,
    )


_blackbox = DeferredBlackBoxRecorder(
    base_dir="runtime/blackbox",
    max_events=50000,
    shutdown_snapshot=_build_shutdown_snapshot,
)

EARLY_INTERRUPT_THRESHOLD_SECONDS = 5.0
MAX_EXTERNAL_INTERRUPT_RECOVERIES = 10
MAX_MAIN_RESTARTS = 5

_run_phase = "initializing"
_run_started_monotonic = time.monotonic()
_RUNTIME_LOCK_PATH = Path("runtime") / "main.lock"
_BLACKBOX_LOG_HANDLER_ATTACHED = False
_BLACKBOX_LOG_MAX_MESSAGE_CHARS = 20000


class _BlackBoxLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Evita bucles si el propio recorder llegara a loguear errores.
        if record.name.startswith("src.utils.blackbox"):
            return
        if not _blackbox.started:
            return
        try:
            message = record.getMessage()
            if len(message) > _BLACKBOX_LOG_MAX_MESSAGE_CHARS:
                message = message[:_BLACKBOX_LOG_MAX_MESSAGE_CHARS] + "..."
            _blackbox.record(
                "log",
                component=record.name or "root",
                level=record.levelname,
                message=message,
                logger=record.name or "root",
            )
        except Exception:
            # Nunca interrumpir flujo principal por errores de telemetría.
            pass


def _attach_blackbox_log_handler() -> None:
    global _BLACKBOX_LOG_HANDLER_ATTACHED
    if _BLACKBOX_LOG_HANDLER_ATTACHED:
        return
    handler = _BlackBoxLogHandler(level=logging.INFO)
    handler.set_name("blackbox-log-forwarder")
    logging.getLogger().addHandler(handler)
    _BLACKBOX_LOG_HANDLER_ATTACHED = True


def _set_run_phase(phase: str) -> None:
    global _run_phase
    _run_phase = phase
    _blackbox.record("run_phase", component="main", phase=phase)


def _run_elapsed_seconds() -> float:
    return max(0.0, time.monotonic() - _run_started_monotonic)


def _emit_final_shutdown_summary() -> None:
    logging.info(
        "Shutdown final: reason=%s | last_exception=%s | componente=%s | signal=%s",
        _shutdown_diag.reason,
        _shutdown_diag.last_exception or "none",
        _shutdown_diag.component,
        _shutdown_diag.signal_name or "none",
    )
    _blackbox.record(
        "shutdown_final",
        component="main",
        last_exception=_shutdown_diag.last_exception or "none",
        signal_name=_shutdown_diag.signal_name or "none",
    )
    _blackbox.dump_summary(
        {
            "reason": _shutdown_diag.reason,
            "last_exception": _shutdown_diag.last_exception,
            "component": _shutdown_diag.component,
            "signal_name": _shutdown_diag.signal_name,
        }
    )


def _set_shutdown_reason(
    reason: str,
    component: str,
    last_exception: str | None = None,
    signal_name: str | None = None,
) -> None:
    _shutdown_diag.reason = reason
    _shutdown_diag.component = component
    if last_exception is not None:
        _shutdown_diag.last_exception = last_exception
    if signal_name is not None:
        _shutdown_diag.signal_name = signal_name
    _blackbox.record(
        "shutdown_reason_updated",
        component=component,
        reason=reason,
        last_exception=_shutdown_diag.last_exception,
        signal_name=_shutdown_diag.signal_name,
    )


async def run() -> None:
    global _run_started_monotonic
    if not _blackbox.started:
        _blackbox.start()
    _blackbox.record("run_enter", component="main")
    _run_started_monotonic = time.monotonic()
    _set_run_phase("initializing")
    load_dotenv()
    settings = AppSettings.load()
    setup_logging(settings.log_level)
    _attach_blackbox_log_handler()
    logging.info("Blackbox activa: %s", _blackbox.path)
    _blackbox.record(
        "settings_loaded",
        component="main",
        enable_telegram=settings.enable_telegram,
        account_mode=settings.pocket_account_mode,
        log_level=settings.log_level,
    )

    loop = asyncio.get_running_loop()
    previous_exception_handler = loop.get_exception_handler()
    loop.set_exception_handler(_build_asyncio_exception_handler(previous_exception_handler))
    shutdown_event = asyncio.Event()
    _set_shutdown_reason("running", "main", last_exception="", signal_name="")
    restore_signal_handlers = _install_signal_handlers(loop, shutdown_event)
    _blackbox.record("asyncio_ready", component="main")

    _confirm_expected_utc_offset(
        signal_timezone=settings.signal_timezone,
        expected_hours=settings.expected_utc_offset_hours,
        enforce=settings.enforce_expected_utc_offset,
    )

    pocket_client = PocketOptionDemoClient(
        account_mode=settings.pocket_account_mode,
        default_asset=settings.default_asset,
        demo_url=settings.pocket_demo_url,
        profile_dir=settings.pocket_profile_dir,
        headless=settings.pocket_headless,
        execute_orders=settings.pocket_execute_orders,
        max_order_amount=settings.pocket_max_order_amount,
        balance_selector=settings.pocket_balance_selector,
        asset_open_selector=settings.pocket_asset_open_selector,
        asset_search_selector=settings.pocket_asset_search_selector,
        asset_result_selector=settings.pocket_asset_result_selector,
        buy_selector=settings.pocket_buy_selector,
        sell_selector=settings.pocket_sell_selector,
        amount_selector=settings.pocket_amount_selector,
    )
    worker_tasks: list[asyncio.Task] = []
    completed_cleanly = False
    try:
        _set_run_phase("starting_browser")
        _blackbox.record("pocket_connect_begin", component="pocket_client")
        await pocket_client.connect()
        _blackbox.record("pocket_connect_ok", component="pocket_client")
        _set_run_phase("running")
        logging.info("Navegador Pocket Option abierto. Resuelve CAPTCHA/login en esa ventana si aparece.")

        balance = await _wait_for_demo_balance(
            pocket_client, timeout_seconds=settings.pocket_balance_wait_seconds
        )
        _blackbox.record("balance_loaded", component="pocket_client", balance=balance)
        print(f"Saldo demo real (Pocket Option): {balance:,.2f}")
        logging.info("Saldo demo real leido desde Pocket Option: %.2f", balance)
        logging.info(
            "Modo ordenes: %s",
            "REAL (click en broker)" if settings.pocket_execute_orders else "SIMULADO (sin click)",
        )

        if not settings.enable_telegram:
            logging.info("APP_ENABLE_TELEGRAM=false. Modo Pocket Option activo.")
            if settings.pocket_keep_browser_open:
                await _terminal_work_mode(pocket_client, settings.default_asset)
            completed_cleanly = True
            return

        if settings.telegram_api_id is None:
            raise ValueError("Falta TELEGRAM_API_ID en .env")

        from src.signals.parser import SignalParser
        from src.telegram.reader import TelegramSignalReader

        logging.info("Iniciando sistema Telegram en cuenta=%s", settings.pocket_account_mode)
        _blackbox.record("telegram_pipeline_init", component="main")
        state_manager = StateManager(dedupe_ttl_seconds=settings.message_dedupe_ttl_seconds)
        global_gale_state = GlobalGaleState(profit_target=2.0)
        message_queue = MessageQueue(maxsize=settings.processing_queue_maxsize)
        parser = SignalParser(
            default_amount=settings.default_amount,
            signal_tz_offset_hours=settings.expected_utc_offset_hours,
            signal_timezone=settings.signal_timezone,
        )

        def _on_signal_processor_fatal_error(reason: str) -> None:
            if shutdown_event.is_set():
                return
            logging.error("SignalProcessor fatal: %s", reason)
            _set_shutdown_reason("internal_error", "signal_processor", last_exception=reason)
            _blackbox.record(
                "signal_processor_fatal_error",
                component="signal_processor",
                reason=reason,
            )
            shutdown_event.set()

        engine = SignalEngine(
            pocket_client=pocket_client,
            martingale_amounts=settings.martingale_amounts,
            martingale_mode=settings.martingale_mode,
            calc_payout_percent=settings.calc_payout_percent,
            calc_increment=settings.calc_increment,
            calc_rule10_balance_threshold=settings.calc_rule10_balance_threshold,
            calc_max_steps=settings.calc_max_steps,
            result_grace_seconds=settings.order_result_grace_seconds,
            reference_utc_offset_hours=settings.expected_utc_offset_hours,
            color_output=settings.color_output,
            signal_late_tolerance_seconds=settings.signal_late_tolerance_seconds,
            global_gale_state=global_gale_state,
            event_recorder=_blackbox.record,
        )
        processor = SignalProcessor(
            message_queue=message_queue,
            parser=parser,
            execution_engine=engine,
            state_manager=state_manager,
            late_tolerance_seconds=settings.signal_late_tolerance_seconds,
            busy_policy=settings.busy_policy,
            default_asset=settings.default_asset,
            single_asset_mode=settings.single_asset_mode,
            override_asset=settings.override_asset,
            override_side=settings.override_side,
            event_recorder=_blackbox.record,
            fatal_error_handler=_on_signal_processor_fatal_error,
        )
        worker_tasks = processor.start()
        _blackbox.record("worker_tasks_started", component="signal_processor", count=len(worker_tasks))

        backfill_minutes = settings.telegram_backfill_minutes
        backfill_limit = settings.telegram_backfill_limit
        if settings.telegram_realtime_only:
            backfill_minutes = 0
            backfill_limit = 1
            logging.info("Modo Telegram realtime puro activo: backfill deshabilitado")
            _blackbox.record(
                "telegram_realtime_only_enabled",
                component="telegram_reader",
            )

        # Crear el reader UNA SOLA VEZ — el cliente Telethon no se recrea nunca.
        # run() maneja la reconexión interna ante caídas de red / cambios de IP (VPN).
        reader = TelegramSignalReader(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            session_name=settings.telegram_session_name,
            source_chats=settings.telegram_source_chats,
            backfill_minutes=backfill_minutes,
            backfill_limit=backfill_limit,
            channel_names=settings.telegram_channel_names,
            restart_after_signal=settings.telegram_restart_after_signal,
        )

        _blackbox.record("reader_start_begin", component="telegram_reader")
        try:
            await reader.run(processor.enqueue_message, shutdown_event)
        except asyncio.CancelledError:
            if shutdown_event.is_set() or _shutdown_diag.reason in {
                "manual_signal",
                "keyboard_interrupt",
            }:
                logging.info(
                    "reader.run() cancelado durante apagado controlado (reason=%s)",
                    _shutdown_diag.reason,
                )
                _blackbox.record(
                    "reader_cancelled_during_shutdown",
                    component="telegram_reader",
                    reason=_shutdown_diag.reason,
                )
            else:
                logging.warning("reader.run() cancelado inesperadamente")
                _blackbox.record(
                    "reader_cancelled_transient",
                    component="telegram_reader",
                    reason="transient_cancelled_error",
                )
        except Exception as exc:
            _set_shutdown_reason(
                "internal_error",
                "telegram_reader",
                last_exception=f"{type(exc).__name__}: {exc}",
            )
            logging.exception("reader.run() terminó con error fatal: %s", exc)
            _blackbox.record(
                "reader_exception",
                component="telegram_reader",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        finally:
            # Desconectar Telegram con timeout
            try:
                await asyncio.wait_for(reader.disconnect(), timeout=5.0)
            except asyncio.TimeoutError:
                logging.warning("Timeout desconectando Telegram (5s), continuando cierre...")
            except Exception as exc:
                logging.debug("Error desconectando Telegram: %s", exc)

        logging.info("Shutdown solicitado, cerrando sistema")
        if _shutdown_diag.reason == "running":
            completed_cleanly = True
    finally:
        pre_shutdown_phase = _run_phase
        _set_run_phase("shutting_down")
        elapsed_seconds = _run_elapsed_seconds()
        _blackbox.record("run_finally_enter", component="main", worker_count=len(worker_tasks))
        for task in worker_tasks:
            task.cancel()
        if worker_tasks:
            _blackbox.record("worker_tasks_cancelled", component="signal_processor", count=len(worker_tasks))

        if worker_tasks:
            # Esperar workers con timeout para no colgar el cierre
            try:
                await asyncio.wait_for(
                    asyncio.gather(*worker_tasks, return_exceptions=True),
                    timeout=10.0
                )
                _blackbox.record("worker_tasks_joined", component="signal_processor", count=len(worker_tasks))
            except asyncio.TimeoutError:
                logging.warning(
                    "Timeout esperando workers (10s), %d tareas aun activas, forzando cierre...",
                    len([t for t in worker_tasks if not t.done()])
                )
                _blackbox.record(
                    "worker_tasks_timeout",
                    component="signal_processor",
                    count=len(worker_tasks),
                    pending=len([t for t in worker_tasks if not t.done()])
                )

        if completed_cleanly:
            _blackbox.record("run_completed_cleanly", component="main")

        snapshot = pocket_client.lifecycle_snapshot()
        early_interrupt_like = (
            elapsed_seconds < EARLY_INTERRUPT_THRESHOLD_SECONDS
            and _shutdown_diag.reason == "running"
            and pre_shutdown_phase in {"initializing", "starting_browser"}
            and not bool(snapshot.get("has_playwright"))
            and not bool(snapshot.get("has_context"))
            and not bool(snapshot.get("has_page"))
        )

        if early_interrupt_like:
            logging.info(
                "Interrupcion temprana en fase=%s (%.2fs): se omite cierre de navegador por no inicializado",
                pre_shutdown_phase,
                elapsed_seconds,
            )
            _blackbox.record(
                "pocket_close_skipped_early_interrupt",
                component="pocket_client",
                phase=pre_shutdown_phase,
                elapsed_seconds=round(elapsed_seconds, 3),
                lifecycle=snapshot,
            )
        else:
            logging.info(
                "Cerrando navegador (shutdown_reason=%s, phase=%s, elapsed=%.2fs)",
                _shutdown_diag.reason,
                pre_shutdown_phase,
                elapsed_seconds,
            )
            _blackbox.record(
                "pocket_close_begin",
                component="pocket_client",
                phase=pre_shutdown_phase,
                elapsed_seconds=round(elapsed_seconds, 3),
                lifecycle=snapshot,
            )
            await pocket_client.close()
            _blackbox.record("pocket_close_done", component="pocket_client")

        restore_signal_handlers()


async def _wait_for_demo_balance(client: PocketOptionDemoClient, timeout_seconds: int = 90) -> float:
    attempts = max(1, timeout_seconds // 3)
    for _ in range(attempts):
        try:
            return await client.get_account_balance()
        except RuntimeError:
            logging.info(
                "Esperando saldo demo real. Si se abrio el navegador, inicia sesion en Pocket Option..."
            )
            await asyncio.sleep(3)

    raise RuntimeError(
        "No se pudo leer el saldo demo real a tiempo. "
        "Abre Pocket Option en modo demo, inicia sesion y vuelve a ejecutar."
    )


async def _terminal_work_mode(client: PocketOptionDemoClient, default_asset: str) -> None:
    print("\nModo trabajo activo. La pagina queda abierta.")
    print("Comandos: [r] saldo | [buy <monto>] | [sell <monto>] | [q] terminar trabajo")

    while True:
        raw = (await asyncio.to_thread(input, "> ")).strip()
        cmd = raw.lower()

        if cmd == "r":
            try:
                balance = await client.get_account_balance()
                print(f"Saldo demo real (Pocket Option): {balance:,.2f}")
            except Exception as exc:
                print(f"No se pudo refrescar saldo: {exc}")
            continue

        if cmd.startswith("buy") or cmd.startswith("sell"):
            try:
                side = "BUY" if cmd.startswith("buy") else "SELL"
                amount = _extract_amount_from_command(raw)
                signal = TradingSignal(
                    asset=default_asset,
                    side=side,
                    expiry_minutes=1,
                    amount=amount,
                    source_text=f"MANUAL_{side}_{amount}",
                    received_at=TradingSignal.now_utc(),
                )
                await client.place_order(signal)
                print(f"Orden {side} enviada para {default_asset} con monto {amount:.2f}")
            except Exception as exc:
                print(f"No se pudo enviar la orden: {exc}")
            continue

        if cmd == "q":
            print("Terminando trabajo. La sesion queda guardada en el perfil persistente.")
            break

        print("Comando no valido. Usa r, buy <monto>, sell <monto> o q.")


def _extract_amount_from_command(raw_cmd: str) -> float:
    parts = raw_cmd.split()
    if len(parts) < 2:
        return 1.0

    value = parts[1].replace(",", ".")
    amount = float(value)
    if amount <= 0:
        raise ValueError("El monto debe ser mayor que 0")
    return amount


def _confirm_expected_utc_offset(signal_timezone: str, expected_hours: int, enforce: bool) -> None:
    """Valida la referencia horaria del sistema de señales sin depender del reloj local del SO."""
    now_utc = datetime.now(timezone.utc)

    if ZoneInfo is None:
        logging.info(
            "ZoneInfo no disponible. Se usará referencia fija UTC%+d para señales.",
            expected_hours,
        )
        return

    try:
        signal_tz = ZoneInfo(signal_timezone)
    except Exception:
        logging.info(
            "No se pudo cargar timezone '%s' en este entorno. Se usará referencia fija UTC%+d.",
            signal_timezone,
            expected_hours,
        )
        return

    signal_now = now_utc.astimezone(signal_tz)
    offset = signal_now.utcoffset()
    signal_offset_hours = int(offset.total_seconds() // 3600) if offset else 0

    if signal_offset_hours == expected_hours:
        logging.info(
            "Referencia horaria confirmada: timezone='%s' offset_actual=UTC%+d",
            signal_timezone,
            signal_offset_hours,
        )
        return

    msg = (
        f"La zona configurada '{signal_timezone}' hoy está en UTC{signal_offset_hours:+d}, "
        f"pero APP_EXPECTED_UTC_OFFSET_HOURS=UTC{expected_hours:+d}. "
        "El parser seguirá priorizando APP_SIGNAL_TIMEZONE para calcular la hora de entrada."
    )
    if enforce:
        logging.warning("APP_ENFORCE_UTC_OFFSET=true, pero el bloqueo fue desactivado por compatibilidad.")
    logging.warning(msg)


def _build_asyncio_exception_handler(previous_handler):
    def _handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        message = str(exc or context.get("message", ""))
        lowered = message.lower()
        _blackbox.record(
            "asyncio_exception_handler",
            component="asyncio",
            message=message,
            error_type=type(exc).__name__ if exc else "none",
        )

        expected_shutdown_noise = (
            "target page, context or browser has been closed" in lowered
            or "connection closed while reading from the driver" in lowered
            or "i/o operation on closed pipe" in lowered
        )
        if expected_shutdown_noise:
            logging.debug("Ruido esperado de cierre de asyncio/playwright suprimido: %s", message)
            return

        if previous_handler is not None:
            previous_handler(loop, context)
            return

        loop.default_exception_handler(context)

    return _handler


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
) -> Callable[[], None]:
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = getattr(signal, "SIGTERM", None)
    previous_sigterm_handler = signal.getsignal(previous_sigterm) if previous_sigterm else None

    def _request_shutdown(sig_name: str) -> None:
        if not shutdown_event.is_set():
            logging.warning("Senal del sistema recibida: %s", sig_name)
            _set_shutdown_reason("manual_signal", "signal_handler", signal_name=sig_name)
            _blackbox.record("signal_received", component="signal_handler", signal_name=sig_name)
            shutdown_event.set()

    def _sync_request_shutdown(sig_name: str) -> None:
        if not shutdown_event.is_set():
            logging.warning("Senal del sistema recibida (sync): %s", sig_name)
            _set_shutdown_reason("manual_signal", "signal_handler", signal_name=sig_name)
            _blackbox.record("signal_received", component="signal_handler", signal_name=sig_name)
            loop.call_soon_threadsafe(shutdown_event.set)

    installed_async_handlers = False
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, lambda s=sig_name: _request_shutdown(s))
            installed_async_handlers = True
        except (NotImplementedError, RuntimeError, ValueError):
            # Some platforms/event loops do not support signal handlers.
            continue

    if not installed_async_handlers:
        def _sync_sigint_handler(signum: int, frame: FrameType | None) -> None:
            elapsed = _run_elapsed_seconds()
            if _run_phase == "starting_browser" and elapsed < EARLY_INTERRUPT_THRESHOLD_SECONDS:
                logging.warning("Interrupcion ignorada durante arranque (no se cierra sistema)")
                _blackbox.record(
                    "startup_interrupt_ignored",
                    component="signal_handler",
                    signal_name="SIGINT",
                    phase=_run_phase,
                    elapsed_seconds=round(elapsed, 3),
                )
                return
            _sync_request_shutdown("SIGINT")

        signal.signal(signal.SIGINT, _sync_sigint_handler)

        if previous_sigterm is not None:
            def _sync_sigterm_handler(signum: int, frame: FrameType | None) -> None:
                _sync_request_shutdown("SIGTERM")

            try:
                signal.signal(previous_sigterm, _sync_sigterm_handler)
            except (ValueError, OSError):
                pass

    def _restore() -> None:
        if not installed_async_handlers:
            try:
                signal.signal(signal.SIGINT, previous_sigint)
            except (ValueError, OSError):
                pass
            if previous_sigterm is not None and previous_sigterm_handler is not None:
                try:
                    signal.signal(previous_sigterm, previous_sigterm_handler)
                except (ValueError, OSError):
                    pass

    return _restore


def _log_exception_origin(prefix: str, exc: BaseException) -> None:
    tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    origin = "unknown"
    if tb:
        frame = tb[-1]
        origin = f"{frame.filename}:{frame.lineno} in {frame.name}"
    _blackbox.record(
        "exception_origin_logged",
        component="main",
        prefix=prefix,
        error_type=type(exc).__name__,
        origin=origin,
        error_message=str(exc),
    )

    logging.error(
        "%s | type=%s | origin=%s | message=%s",
        prefix,
        type(exc).__name__,
        origin,
        exc,
    )


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_single_instance_lock() -> Callable[[], None]:
    _RUNTIME_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _RUNTIME_LOCK_PATH.exists():
        try:
            raw = _RUNTIME_LOCK_PATH.read_text(encoding="utf-8").strip()
            previous_pid = int(raw) if raw else 0
        except Exception:
            previous_pid = 0

        if previous_pid and _pid_exists(previous_pid):
            raise RuntimeError(
                f"Ya hay una instancia activa de main.py (pid={previous_pid}). "
                "Cierra la instancia anterior antes de abrir otra."
            )

        try:
            _RUNTIME_LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass

    _RUNTIME_LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")

    def _release() -> None:
        try:
            if not _RUNTIME_LOCK_PATH.exists():
                return
            owner = _RUNTIME_LOCK_PATH.read_text(encoding="utf-8").strip()
            if owner == str(os.getpid()):
                _RUNTIME_LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass

    return _release


if __name__ == "__main__":
    release_lock = _acquire_single_instance_lock()
    _blackbox.start()
    external_interrupt_recoveries = 0
    restart_attempts = 0

    while True:
        should_restart = False
        summary_emitted = False
        run_started_monotonic = time.monotonic()

        try:
            asyncio.run(run())
        except ValueError as exc:
            _set_shutdown_reason("internal_error", "main", last_exception=f"{type(exc).__name__}: {exc}")
            _blackbox.record("top_level_value_error", component="main", error_message=str(exc))
            print(f"Configuracion incompleta: {exc}")
            print("Crea y completa tu archivo .env a partir de .env.example antes de ejecutar.")
        except RuntimeError as exc:
            _set_shutdown_reason("internal_error", "main", last_exception=f"{type(exc).__name__}: {exc}")
            _blackbox.record("top_level_runtime_error", component="main", error_message=str(exc))
            print(str(exc))
        except KeyboardInterrupt:
            elapsed = time.monotonic() - run_started_monotonic
            _blackbox.record(
                "top_level_keyboard_interrupt",
                component="main",
                active_reason=_shutdown_diag.reason,
                elapsed_seconds=round(elapsed, 3),
            )

            early_external_interrupt = (
                elapsed < EARLY_INTERRUPT_THRESHOLD_SECONDS
                and _shutdown_diag.reason in {"running", "normal_exit"}
                and _run_phase in {"initializing", "starting_browser"}
                and not _shutdown_diag.last_exception
            )
            if early_external_interrupt:
                external_interrupt_recoveries += 1
                restart_attempts += 1
                _set_shutdown_reason(
                    "external_interrupt_suspected",
                    "main",
                    last_exception="KeyboardInterrupt",
                )
                logging.warning(
                    "Posible interrupcion externa del entorno (IDE/restart). elapsed=%.2fs recovery=%s/%s",
                    elapsed,
                    external_interrupt_recoveries,
                    MAX_EXTERNAL_INTERRUPT_RECOVERIES,
                )
                _blackbox.record(
                    "keyboard_interrupt_external_suspected",
                    component="main",
                    elapsed_seconds=round(elapsed, 3),
                    recovery_attempt=external_interrupt_recoveries,
                    restart_attempt=restart_attempts,
                    phase=_run_phase,
                )
                _emit_final_shutdown_summary()
                summary_emitted = True

                if external_interrupt_recoveries >= MAX_EXTERNAL_INTERRUPT_RECOVERIES:
                    logging.error(
                        "Se alcanzo el maximo de recuperaciones por interrupcion externa (%s).",
                        MAX_EXTERNAL_INTERRUPT_RECOVERIES,
                    )
                elif restart_attempts >= MAX_MAIN_RESTARTS:
                    logging.error(
                        "Se alcanzo el maximo de reinicios del runtime principal (%s).",
                        MAX_MAIN_RESTARTS,
                    )
                else:
                    logging.info("Reintentando arranque tras interrupcion externa sospechosa...")
                    should_restart = True
            else:
                external_interrupt_recoveries = 0
                restart_attempts = 0
                if _shutdown_diag.reason in {"running", "normal_exit"}:
                    _set_shutdown_reason("manual_signal", "main", last_exception="KeyboardInterrupt")
                if _shutdown_diag.reason != "normal_exit":
                    logging.warning("KeyboardInterrupt capturado en main")
        except asyncio.CancelledError as exc:
            external_interrupt_recoveries = 0
            restart_attempts = 0
            if _shutdown_diag.reason == "running":
                _set_shutdown_reason(
                    "cancelled_error",
                    "main",
                    last_exception=f"{type(exc).__name__}: {exc}",
                )
            _blackbox.record("top_level_cancelled_error", component="main", error_message=str(exc))
            _log_exception_origin("CancelledError en main (posible cancelacion interna)", exc)
        except Exception as exc:
            external_interrupt_recoveries = 0
            restart_attempts = 0
            _set_shutdown_reason(
                "internal_error",
                "main",
                last_exception=f"{type(exc).__name__}: {exc}",
            )
            _blackbox.record("top_level_unhandled_exception", component="main", error_message=str(exc))
            _log_exception_origin("Error inesperado en main", exc)
        else:
            external_interrupt_recoveries = 0
            restart_attempts = 0
            if _shutdown_diag.reason == "running":
                _set_shutdown_reason("normal_exit", "main")
        finally:
            if not summary_emitted:
                _emit_final_shutdown_summary()

        if not should_restart and _shutdown_diag.reason in {"internal_error", "cancelled_error"}:
            restart_attempts += 1
            if restart_attempts <= MAX_MAIN_RESTARTS:
                logging.warning(
                    "Reinicio automatico por error (%s). intento=%s/%s",
                    _shutdown_diag.reason,
                    restart_attempts,
                    MAX_MAIN_RESTARTS,
                )
                _blackbox.record(
                    "auto_restart_on_error",
                    component="main",
                    reason=_shutdown_diag.reason,
                    restart_attempt=restart_attempts,
                    max_restarts=MAX_MAIN_RESTARTS,
                )
                should_restart = True
            else:
                logging.error(
                    "Se alcanzo el maximo de reinicios por error (%s).",
                    MAX_MAIN_RESTARTS,
                )

        if should_restart:
            continue
        break

    release_lock()
