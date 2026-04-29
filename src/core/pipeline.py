import asyncio
import logging
import shutil
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.core.models import TradingSignal
from src.pocket_option.assets import canonicalize_pocket_asset
from src.signals.parser import SignalParser
from src.telegram.message_types import TelegramInboundMessage


@dataclass(frozen=True)
class QueuedSignal:
    envelope: TelegramInboundMessage
    signal: TradingSignal
    delay_seconds: float


class MessageQueue:
    def __init__(self, maxsize: int = 500) -> None:
        self._queue: asyncio.Queue[TelegramInboundMessage] = asyncio.Queue(maxsize=maxsize)

    async def put(self, envelope: TelegramInboundMessage) -> None:
        try:
            self._queue.put_nowait(envelope)
            return
        except asyncio.QueueFull:
            pass

        # latest-wins: si la cola está llena, descartamos el más viejo para no bloquear el handler.
        dropped: TelegramInboundMessage | None = None
        try:
            dropped = self._queue.get_nowait()
            self._queue.task_done()
        except asyncio.QueueEmpty:
            dropped = None

        if dropped is not None:
            logging.warning(
                "MessageQueue llena: descartado mensaje viejo chat_id=%s msg_id=%s para priorizar realtime",
                dropped.chat_id,
                dropped.message_id,
            )

        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            # Fallback ultra raro por carrera: no bloquear el producer en ningún caso.
            logging.warning(
                "MessageQueue sigue llena tras descarte; mensaje nuevo descartado chat_id=%s msg_id=%s",
                envelope.chat_id,
                envelope.message_id,
            )

    async def get(self) -> TelegramInboundMessage:
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()


class StateManager:
    def __init__(self, dedupe_ttl_seconds: int = 6 * 60 * 60) -> None:
        self._dedupe_ttl_seconds = max(60, dedupe_ttl_seconds)
        self._seen: dict[str, datetime] = {}
        self._order: deque[str] = deque()
        self._active_count: int = 0  # número de señales ejecutándose ahora
        # CRITICAL FIX: Track active channels to prevent simultaneous signals from same channel
        self._active_channels: set[int] = set()  # set of chat_ids currently executing
        # CRITICAL FIX: Use lock for atomic channel operations (prevent race conditions)
        self._channel_lock = asyncio.Lock()

    @property
    def execution_active(self) -> bool:
        return self._active_count > 0

    def inc_active(self) -> None:
        self._active_count += 1

    def dec_active(self) -> None:
        self._active_count = max(0, self._active_count - 1)

    @property
    def active_count(self) -> int:
        return self._active_count

    async def is_channel_active(self, chat_id: int) -> bool:
        """Check if a channel (chat_id) already has an active signal (atomic operation)."""
        async with self._channel_lock:
            return chat_id in self._active_channels

    async def mark_channel_active(self, chat_id: int) -> None:
        """Mark a channel as having an active signal (atomic operation)."""
        async with self._channel_lock:
            self._active_channels.add(chat_id)

    async def mark_channel_inactive(self, chat_id: int) -> None:
        """Mark a channel as no longer having an active signal (atomic operation)."""
        async with self._channel_lock:
            self._active_channels.discard(chat_id)

    def is_duplicate(self, key: str, now_utc: datetime) -> bool:
        self._evict_old(now_utc)
        if key in self._seen:
            return True

        self._seen[key] = now_utc
        self._order.append(key)
        return False

    def _evict_old(self, now_utc: datetime) -> None:
        cutoff = now_utc - timedelta(seconds=self._dedupe_ttl_seconds)
        while self._order:
            oldest = self._order[0]
            ts = self._seen.get(oldest)
            if ts is None:
                self._order.popleft()
                continue
            if ts >= cutoff:
                break
            self._order.popleft()
            self._seen.pop(oldest, None)


class GlobalGaleState:
    """Persistent martingale state that survives across all signals.
    Only resets on WIN. Always targets +$2 profit from cycle start balance."""
    
    def __init__(self, profit_target: float = 2.0) -> None:
        self._profit_target = profit_target
        self._is_active: bool = False
        self._current_step: int = 0
        self._cycle_start_balance: float = 0.0
        self._accumulated_loss: float = 0.0
    
    @property
    def is_active(self) -> bool:
        return self._is_active
    
    @property
    def current_step(self) -> int:
        return self._current_step
    
    @property
    def cycle_start_balance(self) -> float:
        return self._cycle_start_balance
    
    @property
    def accumulated_loss(self) -> float:
        return self._accumulated_loss
    
    @property
    def target_balance(self) -> float:
        return self._cycle_start_balance + self._profit_target
    
    def start_new_cycle(self, start_balance: float) -> None:
        """Start a fresh gale cycle from current balance."""
        self._is_active = True
        self._current_step = 0
        self._cycle_start_balance = start_balance
        self._accumulated_loss = 0.0
        logging.info(
            "GlobalGaleState: INICIO de ciclo con balance=%.2f target=%.2f",
            self._cycle_start_balance,
            self.target_balance,
        )
    
    def record_loss(self, amount: float) -> None:
        """Record a loss and advance to next gale step."""
        self._accumulated_loss += amount
        self._current_step += 1
        self._is_active = True
        logging.info(
            "GlobalGaleState: LOSS registrada amount=%.2f step=%d accumulated_loss=%.2f",
            amount,
            self._current_step,
            self._accumulated_loss,
        )
    
    def reset_for_new_signal(self, current_balance: float) -> None:
        """Llamar al inicio de cada señal nueva.
        - Siempre arranca en ENTRADA (step=0).
        - Si hay pérdidas acumuladas de señales anteriores, las CONSERVA para el cálculo de montos.
        - Solo reinicia todo al recibir un WIN.
        """
        if self._is_active and self._accumulated_loss > 0:
            # Hay pérdidas previas: mantener accumulated_loss y cycle_start para el calculador,
            # pero resetear el paso para que la nueva señal empiece en ENTRADA.
            self._current_step = 0
            logging.info(
                "GlobalGaleState: Nueva señal con pérdidas acumuladas=%.2f | step=0 | target=%.2f",
                self._accumulated_loss,
                self.target_balance,
            )
        else:
            # Sin pérdidas previas: ciclo limpio
            self.start_new_cycle(current_balance)

    def record_win(self) -> None:
        """Reset gale state after a WIN."""
        logging.info(
            "GlobalGaleState: WIN - reseteo completo desde step=%d accumulated_loss=%.2f",
            self._current_step,
            self._accumulated_loss,
        )
        self._is_active = False
        self._current_step = 0
        self._cycle_start_balance = 0.0
        self._accumulated_loss = 0.0


class SignalProcessor:
    def __init__(
        self,
        message_queue: MessageQueue,
        parser: SignalParser,
        execution_engine,
        state_manager: StateManager,
        late_tolerance_seconds: int,
        busy_policy: str,
        default_asset: str,
        single_asset_mode: bool,
        override_asset: str = "",
        override_side: str | None = None,
        event_recorder: Callable[..., None] | None = None,
        fatal_error_handler: Callable[[str], None] | None = None,
    ) -> None:
        self._message_queue = message_queue
        self._parser = parser
        self._execution_engine = execution_engine
        self._state_manager = state_manager
        self._late_tolerance_seconds = max(0, late_tolerance_seconds)
        self._busy_policy = busy_policy if busy_policy in {"queue", "ignore_if_busy"} else "queue"
        self._channel_queues: dict[int, asyncio.Queue[QueuedSignal]] = {}
        self._channel_workers: dict[int, asyncio.Task] = {}
        self._channel_workers_lock = asyncio.Lock()
        self._execution_lock = asyncio.Lock()
        self._channel_priority: dict[int, int] = {}
        self._single_asset_mode = single_asset_mode
        self._default_asset = canonicalize_pocket_asset(default_asset, default_asset="EURUSD OTC")
        self._override_asset = canonicalize_pocket_asset(override_asset, default_asset="").strip()
        self._override_side = override_side if override_side in {"BUY", "SELL"} else None
        # Ventana dura de seguridad para evitar ejecutar señales inviables.
        self._max_early_signal_seconds = 300.0
        self._hard_late_signal_seconds = min(10.0, float(self._late_tolerance_seconds))
        self._event_recorder = event_recorder
        self._fatal_error_handler = fatal_error_handler

    def _request_restart(self, reason: str) -> None:
        if self._fatal_error_handler is None:
            return
        try:
            self._fatal_error_handler(reason)
        except Exception:
            pass

    def _priority_rank(self, envelope: TelegramInboundMessage) -> int:
        source = (envelope.source_name or "").strip().lower()
        if "j_zwrpe_texknjcx" in source or "vip trader a" in source:
            return 0
        if "xigjcbsear9jn2rh" in source or "smart signals" in source:
            return 1
        return 99

    async def _wait_for_priority_turn(self, chat_id: int, my_rank: int) -> None:
        if my_rank <= 0:
            return

        while True:
            higher_pending = False
            for queued_chat_id, queued in self._channel_queues.items():
                if queued_chat_id == chat_id or queued.qsize() <= 0:
                    continue
                queued_rank = self._channel_priority.get(queued_chat_id, 99)
                if queued_rank < my_rank:
                    higher_pending = True
                    break

            if not higher_pending and not self._execution_lock.locked():
                return

            await asyncio.sleep(0.1)

    async def enqueue_message(self, envelope: TelegramInboundMessage) -> None:
        await self._message_queue.put(envelope)

    def start(self) -> list[asyncio.Task]:
        return [
            asyncio.create_task(self._process_loop(), name="signal-processor-loop"),
        ]

    async def _process_loop(self) -> None:
        while True:
            envelope = await self._message_queue.get()
            try:
                await self._process_envelope(envelope)
            except Exception as exc:
                logging.exception("Fallo procesando mensaje Telegram: %s", exc)
            finally:
                self._message_queue.task_done()

    async def _enqueue_channel_signal(self, item: QueuedSignal) -> None:
        chat_id = item.envelope.chat_id
        channel_name = item.envelope.source_name or str(chat_id)
        self._channel_priority[chat_id] = self._priority_rank(item.envelope)

        async with self._channel_workers_lock:
            queue = self._channel_queues.get(chat_id)
            if queue is None:
                queue = asyncio.Queue(maxsize=1)
                self._channel_queues[chat_id] = queue

            worker = self._channel_workers.get(chat_id)
            if worker is None or worker.done():
                worker = asyncio.create_task(
                    self._worker_for_channel(chat_id),
                    name=f"signal-channel-worker-{chat_id}",
                )
                self._channel_workers[chat_id] = worker
                logging.info("Worker iniciado canal='%s'", channel_name)

        # Cola inteligente por canal: conservar solo la señal más reciente.
        if queue.full():
            try:
                dropped = queue.get_nowait()
                queue.task_done()
                logging.info(
                    "Señal reemplazada (más reciente llegó): canal='%s' old_msg=%s new_msg=%s",
                    channel_name,
                    dropped.envelope.message_id,
                    item.envelope.message_id,
                )
            except asyncio.QueueEmpty:
                pass

        await queue.put(item)
        channel_queue_size = queue.qsize()
        total_pending = sum(q.qsize() for q in self._channel_queues.values())

        self._emit_event(
            "trade_signal_queued",
            channel=channel_name,
            chat_id=item.envelope.chat_id,
            message_id=item.envelope.message_id,
            asset=item.signal.asset,
            side=item.signal.side,
            queue_size=channel_queue_size,
            total_pending=total_pending,
            entry_utc=item.signal.execute_at_utc.isoformat() if item.signal.execute_at_utc else "NOW",
        )

    async def _worker_for_channel(self, chat_id: int) -> None:
        queue = self._channel_queues.get(chat_id)
        if queue is None:
            return

        channel_name = str(chat_id)
        while True:
            item = await queue.get()

            channel_name = item.envelope.source_name or str(chat_id)
            try:
                my_rank = self._channel_priority.get(chat_id, 99)
                await self._wait_for_priority_turn(chat_id, my_rank)

                async with self._execution_lock:
                    logging.info(
                        "Iniciando control de entrada canal='%s' asset=%s",
                        channel_name,
                        item.signal.asset,
                    )
                    await self._run_signal_task(item)
            finally:
                queue.task_done()

    async def _run_signal_task(self, item: QueuedSignal) -> None:
        self._state_manager.inc_active()
        # CRITICAL FIX: Mark channel as active (atomic) to prevent simultaneous signals from same channel
        await self._state_manager.mark_channel_active(item.envelope.chat_id)
        logging.info(
            "[PARALELO] Iniciando señal canal='%s' asset=%s side=%s activas=%d",
            item.envelope.source_name or str(item.envelope.chat_id),
            item.signal.asset,
            item.signal.side,
            self._state_manager.active_count,
        )
        try:
            await self._execution_engine.execute_signal(item.signal)
        except RuntimeError as exc:
            message = str(exc)
            if (
                "cancelada por desalineacion de activo" in message
                or "cancelada" in message
                or "ignorada" in message
            ):
                self._emit_event(
                    "trade_signal_cancelled",
                    channel=item.envelope.source_name or str(item.envelope.chat_id),
                    message_id=item.envelope.message_id,
                    asset=item.signal.asset,
                    side=item.signal.side,
                    reason=message,
                )
                logging.warning(
                    "Senal cancelada canal='%s' msg_id=%s motivo=%s",
                    item.envelope.source_name or str(item.envelope.chat_id),
                    item.envelope.message_id,
                    message,
                )
            else:
                logging.exception(
                    "Error en ejecucion de señal canal='%s' msg_id=%s: %s",
                    item.envelope.source_name or str(item.envelope.chat_id),
                    item.envelope.message_id,
                    exc,
                )
            self._request_restart(
                f"RuntimeError en ejecucion de señal canal={item.envelope.source_name or item.envelope.chat_id} "
                f"msg_id={item.envelope.message_id}: {message}"
            )
        except Exception as exc:
            logging.exception(
                "Error en ejecucion de señal canal='%s' msg_id=%s: %s",
                item.envelope.source_name or str(item.envelope.chat_id),
                item.envelope.message_id,
                exc,
            )
            self._request_restart(
                f"Exception en ejecucion de señal canal={item.envelope.source_name or item.envelope.chat_id} "
                f"msg_id={item.envelope.message_id}: {type(exc).__name__}: {exc}"
            )
        finally:
            self._state_manager.dec_active()
            # CRITICAL FIX: Unmark channel when execution completes (atomic)
            await self._state_manager.mark_channel_inactive(item.envelope.chat_id)

    async def _process_envelope(self, envelope: TelegramInboundMessage) -> None:
        now_utc = datetime.now(timezone.utc)
        msg_utc = envelope.message_date_utc.astimezone(timezone.utc)
        delay = (now_utc - msg_utc).total_seconds()
        key = f"{envelope.chat_id}:{envelope.message_id}"

        if self._state_manager.is_duplicate(key, now_utc):
            self._log_decision("ignorado_por_duplicado", envelope, msg_utc, delay)
            return

        signal = self._parser.parse(envelope.text, received_at_utc=msg_utc)
        if signal is None:
            self._log_decision("ignorado_sin_senal", envelope, msg_utc, delay)
            logging.info(
                "Parser no detecto senal en msg_id=%s canal='%s' texto='%s'",
                envelope.message_id,
                envelope.source_name or str(envelope.chat_id),
                (envelope.text or "").replace("\n", " ")[:220],
            )
            return

        logging.info(
            "Senal parseada msg_id=%s canal='%s' asset=%s side=%s exp=%sm amount=%.2f entry_utc=%s mg_steps=%d",
            envelope.message_id,
            envelope.source_name or str(envelope.chat_id),
            signal.asset,
            signal.side,
            signal.expiry_minutes,
            signal.amount,
            signal.execute_at_utc.isoformat() if signal.execute_at_utc else "NOW",
            len(signal.martingale_execute_at_utc),
        )
        self._emit_event(
            "trade_signal_parsed",
            channel=envelope.source_name or str(envelope.chat_id),
            chat_id=envelope.chat_id,
            message_id=envelope.message_id,
            asset=signal.asset,
            side=signal.side,
            expiry_minutes=signal.expiry_minutes,
            amount=signal.amount,
            entry_utc=signal.execute_at_utc.isoformat() if signal.execute_at_utc else "NOW",
            mg_steps=len(signal.martingale_execute_at_utc),
        )

        import dataclasses
        replacement_fields = {"source_name": envelope.source_name}
        if self._override_asset:
            replacement_fields["asset"] = self._override_asset
        if self._override_side:
            replacement_fields["side"] = self._override_side
        signal = dataclasses.replace(signal, **replacement_fields)

        if self._override_asset or self._override_side:
            logging.info(
                "Override aplicado msg_id=%s asset=%s side=%s",
                envelope.message_id,
                signal.asset,
                signal.side,
            )

        if self._single_asset_mode:
            parsed_asset = canonicalize_pocket_asset(signal.asset, default_asset=self._default_asset)
            if parsed_asset != self._default_asset:
                self._log_decision("ignorado_por_activo", envelope, msg_utc, delay)
                logging.info(
                    "Senal ignorada por modo un par: asset=%s default=%s msg_id=%s",
                    parsed_asset,
                    self._default_asset,
                    envelope.message_id,
                )
                return

        execute_at = signal.execute_at_utc or now_utc
        time_to_entry = (execute_at - now_utc).total_seconds()

        if time_to_entry > self._max_early_signal_seconds:
            self._log_decision("senal_temprana_aceptada", envelope, msg_utc, delay)
            logging.info(
                "Senal temprana aceptada: msg_id=%s faltan=%.1fs max_ref=%.1fs entry_utc=%s",
                envelope.message_id,
                time_to_entry,
                self._max_early_signal_seconds,
                execute_at.isoformat(),
            )

        if time_to_entry < -self._hard_late_signal_seconds:
            self._log_decision("ignorado_por_entrada_expirada", envelope, msg_utc, delay)
            logging.info(
                "Senal ignorada por entrada expirada: msg_id=%s atraso=%.1fs max=%.1fs entry_utc=%s",
                envelope.message_id,
                abs(time_to_entry),
                self._hard_late_signal_seconds,
                execute_at.isoformat(),
            )
            return

        logging.info(
            "Senal aceptada por ventana de entrada: msg_id=%s falta=%.1fs asset=%s side=%s entry_utc=%s",
            envelope.message_id,
            time_to_entry,
            signal.asset,
            signal.side,
            execute_at.isoformat(),
        )

        total_pending = sum(q.qsize() for q in self._channel_queues.values())
        if self._busy_policy == "ignore_if_busy" and (
            self._state_manager.execution_active or total_pending > 0
        ):
            self._log_decision("ignorado_por_sistema_ocupado", envelope, msg_utc, delay)
            return

        await self._enqueue_channel_signal(
            QueuedSignal(
                envelope=envelope,
                signal=signal,
                delay_seconds=delay,
            )
        )
        self._log_decision("procesado", envelope, msg_utc, delay)

    def _log_decision(
        self,
        action: str,
        envelope: TelegramInboundMessage,
        msg_utc: datetime,
        delay: float,
    ) -> None:
        logging.info(
            "msg_id=%s chat_id=%s canal='%s' msg_utc=%s delay_s=%.3f action=%s",
            envelope.message_id,
            envelope.chat_id,
            envelope.source_name or str(envelope.chat_id),
            msg_utc.isoformat(),
            delay,
            action,
        )

    def _emit_event(self, event: str, **fields: Any) -> None:
        if self._event_recorder is None:
            return
        try:
            self._event_recorder(event, component="signal_processor", **fields)
        except Exception:
            pass
