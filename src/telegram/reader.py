import asyncio
import inspect
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, cast

from telethon import TelegramClient, events
from telethon.errors import InviteHashExpiredError, InviteHashInvalidError, UserAlreadyParticipantError
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.utils import get_peer_id
from src.telegram.message_types import TelegramInboundMessage


MessageHandler = Callable[[TelegramInboundMessage], Awaitable[None]]

# Intervalo del ping keep-alive (segundos)
_KEEP_ALIVE_INTERVAL = 60
# Tiempo máximo de espera entre reintentos de reconexión (segundos)
_MAX_RETRY_SECONDS = 30
_INVITE_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)", re.IGNORECASE)


class TelegramSignalReader:
    """
    Cliente Telegram persistente con reconexión automática blindada.

    El TelegramClient se crea UNA SOLA VEZ en __init__ con auto_reconnect=True
    y un numero muy alto de reintentos. Nunca se recrea el cliente.

    Cuando la conexión cae (VPN, corte de red, etc.) el método `run()` detecta
    el error, espera y llama de nuevo a `run_until_disconnected()` sobre el
    MISMO cliente, reutilizando la sesión guardada en disco.

    Uso desde main.py:
        reader = TelegramSignalReader(...)        # crear una sola vez
        await reader.run(on_message, shutdown_event)  # loop blindado
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str,
        source_chats: Iterable[str],
        backfill_minutes: float = 15,
        backfill_limit: int = 40,
        channel_names: Dict[str, str] | None = None,
        restart_after_signal: bool = False,
    ) -> None:
        self._source_chats = list(source_chats)
        # Cliente creado una sola vez con reconnect infinito
        self._client = TelegramClient(
            session_name,
            api_id,
            api_hash,
            auto_reconnect=True,
            connection_retries=10**9,
            retry_delay=3,
        )
        self._backfill_minutes = max(0.0, float(backfill_minutes))
        self._backfill_limit = max(1, backfill_limit)
        # raw_chat_key -> display name (ej: "@viptrader" -> "VIP TRADER A")
        self._channel_names: Dict[str, str] = channel_names or {}
        self._restart_after_signal = restart_after_signal
        self._reconnect_lock = asyncio.Lock()
        self._last_forced_reconnect_ts = 0.0
        # chat_id (int) -> display name resuelto en tiempo de ejecución
        self._id_to_name: Dict[int, str] = {}
        # Tareas de despacho al pipeline para no bloquear el handler de Telethon
        self._dispatch_tasks: set[asyncio.Task] = set()
        # Handlers registrados (solo se registran una vez)
        self._handlers_registered = False

    # ------------------------------------------------------------------
    # API pública principal
    # ------------------------------------------------------------------

    async def _await_if_needed(self, result: Any) -> Any:
        if inspect.isawaitable(result):
            return await result
        return result

    async def _run_until_disconnected(self) -> None:
        await self._await_if_needed(self._client.run_until_disconnected())

    async def _disconnect_with_timeout(self, timeout_seconds: float) -> None:
        result = self._client.disconnect()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, timeout=timeout_seconds)

    async def _dispatch_message(self, on_message: MessageHandler, envelope: TelegramInboundMessage) -> None:
        await on_message(envelope)

    def _schedule_dispatch(self, on_message: MessageHandler, envelope: TelegramInboundMessage) -> None:
        task = asyncio.create_task(
            self._dispatch_message(on_message, envelope),
            name=f"telegram-dispatch-{envelope.chat_id}-{envelope.message_id}",
        )
        self._dispatch_tasks.add(task)

        def _done_callback(done_task: asyncio.Task) -> None:
            self._dispatch_tasks.discard(done_task)
            try:
                exc = done_task.exception()
                if exc is not None:
                    logging.exception(
                        "Fallo despachando mensaje Telegram chat_id=%s msg_id=%s",
                        envelope.chat_id,
                        envelope.message_id,
                        exc_info=exc,
                    )
            except asyncio.CancelledError:
                pass

        task.add_done_callback(_done_callback)

    async def _force_soft_reconnect(self, shutdown_event: asyncio.Event, reason: str) -> None:
        if shutdown_event.is_set() or not self._restart_after_signal:
            return

        async with self._reconnect_lock:
            if shutdown_event.is_set() or not self._client.is_connected():
                return

            now_ts = time.monotonic()
            if now_ts - self._last_forced_reconnect_ts < 2.0:
                return
            self._last_forced_reconnect_ts = now_ts

            logging.info("Telegram: reinicio suave de conexion (%s)", reason)
            try:
                await self._disconnect_with_timeout(timeout_seconds=2.0)
            except Exception as exc:
                logging.debug("Telegram: fallo reinicio suave (%s)", exc)

    async def run(
        self,
        on_message: MessageHandler,
        shutdown_event: asyncio.Event,
        retry_seconds: int = 3,
    ) -> None:
        """
        Loop blindado de reconexión. Llama a este método desde main.py
        en lugar del antiguo start(). No recrea el cliente en ningún momento.
        """
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Sesion Telegram no autorizada. Reautentica TELEGRAM_SESSION_NAME antes de iniciar."
            )

        resolved_chats = await self._resolve_source_chats()
        if not resolved_chats:
            raise RuntimeError(
                "No se pudo resolver ningun chat origen de Telegram. "
                "Revisa TELEGRAM_SOURCE_CHATS (@user, link, id o telefono en contactos)."
            )

        # Registrar handlers una sola vez
        if not self._handlers_registered:
            self._register_handler(on_message, resolved_chats, shutdown_event)
            self._handlers_registered = True

        # Lanzar keep-alive en background
        keep_alive_task = asyncio.create_task(
            self._keep_alive(shutdown_event),
            name="telegram-keep-alive",
        )

        current_retry = max(3, retry_seconds)

        try:
            while not shutdown_event.is_set():
                try:
                    logging.info("Telegram: conectado. Escuchando señales...")
                    if self._backfill_minutes > 0:
                        asyncio.create_task(
                            self._process_recent_messages(on_message, resolved_chats),
                            name="telegram-backfill",
                        )

                    # Ejecutar run_until_disconnected con soporte para shutdown rápido
                    run_task = asyncio.create_task(
                        self._run_until_disconnected(),
                        name="telegram-run-until-disconnected"
                    )
                    shutdown_wait_task = asyncio.create_task(
                        shutdown_event.wait(),
                        name="telegram-shutdown-wait"
                    )
                    
                    done, pending = await asyncio.wait(
                        {run_task, shutdown_wait_task},
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    # Si shutdown fue activado, desconectar activamente el cliente
                    # para forzar la salida de run_until_disconnected
                    if shutdown_event.is_set():
                        logging.info("Telegram: shutdown detectado, desconectando cliente...")
                        if self._client.is_connected():
                            try:
                                await self._disconnect_with_timeout(timeout_seconds=2.0)
                            except Exception as exc:
                                logging.debug("Error desconectando en shutdown: %s", exc)
                    
                    # Cancelar tareas pendientes
                    for task in pending:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                    
                    # Si shutdown fue activado, salir inmediatamente
                    if shutdown_event.is_set():
                        break
                    
                    # run_until_disconnected() retornó limpiamente
                    logging.warning("Telegram: desconectado inesperadamente")

                except asyncio.CancelledError:
                    if shutdown_event.is_set():
                        logging.info("Telegram: cancelado durante apagado controlado")
                        break
                    logging.warning("Telegram: CancelledError transitorio, reconectando...")

                except Exception as exc:
                    if shutdown_event.is_set():
                        break
                    logging.warning(
                        "Telegram: error de conexión (%s: %s). Reconectando en %ds...",
                        type(exc).__name__,
                        exc,
                        current_retry,
                    )

                if shutdown_event.is_set():
                    break

                # Esperar antes de reconectar (respeta shutdown)
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=current_retry)
                    break  # shutdown llegó durante la espera
                except asyncio.TimeoutError:
                    pass

                current_retry = min(current_retry * 2, _MAX_RETRY_SECONDS)

                # Reconectar el cliente existente (NO recrear)
                try:
                    if not self._client.is_connected():
                        logging.info("Telegram: reconectando cliente...")
                        await self._client.connect()
                except Exception as exc:
                    logging.warning("Telegram: fallo al reconectar (%s), reintentando...", exc)
        finally:
            keep_alive_task.cancel()
            await asyncio.gather(keep_alive_task, return_exceptions=True)
            if self._dispatch_tasks:
                for task in list(self._dispatch_tasks):
                    task.cancel()
                await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
                self._dispatch_tasks.clear()

    async def disconnect(self) -> None:
        """Desconectar limpiamente el cliente."""
        try:
            # Intentar desconexión con timeout para no bloquear el cierre
            await self._disconnect_with_timeout(timeout_seconds=3.0)
            logging.info("Telegram: desconectado limpiamente")
        except asyncio.TimeoutError:
            logging.warning("Telegram: timeout en disconnect (3s), continuando cierre...")
        except Exception as exc:
            logging.debug("Telegram: error en disconnect (%s), ignorando...", exc)

    # ------------------------------------------------------------------
    # Método legacy (compatibilidad con código anterior si existe)
    # ------------------------------------------------------------------

    async def start(self, on_message: MessageHandler) -> None:
        """Deprecated: usar run() con shutdown_event para reconexión blindada."""
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Sesion Telegram no autorizada. Reautentica TELEGRAM_SESSION_NAME antes de iniciar."
            )
        resolved_chats = await self._resolve_source_chats()
        if not resolved_chats:
            raise RuntimeError(
                "No se pudo resolver ningun chat origen de Telegram. "
                "Revisa TELEGRAM_SOURCE_CHATS (@user, link, id o telefono en contactos)."
            )

        logging.info("Conectado a Telegram. Escuchando: %s", ", ".join(self._source_chats))

        if not self._handlers_registered:
            self._register_handler(on_message, resolved_chats, asyncio.Event())
            self._handlers_registered = True

        if self._backfill_minutes > 0:
            asyncio.create_task(self._process_recent_messages(on_message, resolved_chats))

        await self._run_until_disconnected()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _register_handler(
        self,
        on_message: MessageHandler,
        resolved_chats: list[Any],
        shutdown_event: asyncio.Event,
    ) -> None:
        @self._client.on(events.NewMessage(chats=resolved_chats))
        async def _handler(event: events.NewMessage.Event) -> None:
            text = (event.raw_text or "").strip()
            if not text or event.date is None:
                return

            chat_id = event.chat_id or 0
            source_name = self._id_to_name.get(chat_id, "")
            now_utc = datetime.now(timezone.utc)
            ingress_lag = (now_utc - event.date.astimezone(timezone.utc)).total_seconds()
            logging.info(
                "Telegram IN canal='%s' chat_id=%s msg_id=%s chars=%s lag_in=%.3fs",
                source_name or str(chat_id),
                chat_id,
                event.id,
                len(text),
                ingress_lag,
            )
            envelope = TelegramInboundMessage(
                chat_id=chat_id,
                message_id=event.id,
                text=text,
                message_date_utc=event.date.astimezone(timezone.utc),
                received_at_utc=now_utc,
                source_name=source_name,
            )
            self._schedule_dispatch(on_message, envelope)

            if self._restart_after_signal:
                asyncio.create_task(
                    self._force_soft_reconnect(
                        shutdown_event,
                        reason=f"msg_id={event.id} chat_id={chat_id}",
                    ),
                    name=f"telegram-soft-reconnect-{chat_id}-{event.id}",
                )

        logging.info("Telegram: handler de mensajes registrado")

    async def _keep_alive(self, shutdown_event: asyncio.Event) -> None:
        """Ping periódico para detectar desconexiones silenciosas (ej: cambio de IP por VPN)."""
        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=_KEEP_ALIVE_INTERVAL)
                break  # shutdown
            except asyncio.TimeoutError:
                pass

            try:
                await self._client.get_me()
                logging.debug("Telegram keep-alive: OK")
            except Exception as exc:
                logging.warning("Telegram keep-alive: ping fallido (%s)", exc)

    async def _process_recent_messages(
        self,
        on_message: MessageHandler,
        resolved_chats: Iterable[Any],
    ) -> None:
        if self._backfill_minutes <= 0:
            return

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self._backfill_minutes)

        for chat in resolved_chats:
            try:
                entity = chat
            except Exception as exc:
                logging.warning("No se pudo resolver chat para backfill (%s): %s", chat, exc)
                continue

            recovered = 0
            messages: list[TelegramInboundMessage] = []
            async for msg in self._client.iter_messages(entity, limit=self._backfill_limit):
                if not msg.date or msg.date < cutoff:
                    break
                text = (msg.raw_text or "").strip()
                if not text:
                    continue
                messages.append(
                    TelegramInboundMessage(
                        chat_id=getattr(entity, "id", 0),
                        message_id=msg.id,
                        text=text,
                        message_date_utc=msg.date.astimezone(timezone.utc),
                        received_at_utc=datetime.now(timezone.utc),
                        source_name=self._id_to_name.get(getattr(entity, "id", 0), ""),
                    )
                )

            for envelope in reversed(messages):
                self._schedule_dispatch(on_message, envelope)
                recovered += 1

            if recovered:
                logging.info(
                    "Backfill Telegram: %s mensajes recientes leidos en %s",
                    recovered,
                    getattr(entity, "title", None) or getattr(entity, "id", "chat"),
                )

    async def _resolve_source_chats(self) -> list[Any]:
        resolved: list[Any] = []

        for raw in self._source_chats:
            chat = (raw or "").strip()
            if not chat:
                continue

            entity = None
            try:
                entity = await self._client.get_entity(chat)
            except Exception:
                pass

            if entity is None:
                entity = await self._resolve_invite_link(chat)

            if entity is None:
                normalized_phone = _normalize_phone(chat)
                if not normalized_phone:
                    logging.warning("No se pudo resolver chat de Telegram: %s", chat)
                    continue

                entity = await self._find_dialog_by_phone(normalized_phone)
                if entity is not None:
                    logging.info("Chat resuelto por telefono: %s", normalized_phone)
                else:
                    logging.warning(
                        "No se encontro chat por telefono %s. Guarda el numero en contactos de Telegram y escribe primero.",
                        normalized_phone,
                    )
                    continue

            resolved.append(entity)

            try:
                input_peer = await self._client.get_input_entity(cast(Any, entity))
                resolved[-1] = input_peer
                input_peer_id = get_peer_id(input_peer)
            except Exception as exc:
                # Keep the original entity as fallback; Telethon may still accept it.
                input_peer_id = 0
                logging.warning(
                    "No se pudo canonizar peer para '%s' (%s): %s",
                    chat,
                    type(entity).__name__,
                    exc,
                )

            # Mapear chat_id -> nombre de canal (para source_name en envelopes)
            entity_id: int = getattr(entity, "id", 0)
            if entity_id:
                # Prioridad: nombre configurado en TELEGRAM_CHANNEL_NAMES
                display = self._channel_names.get(chat, "")
                if not display:
                    # Fallback: título del grupo o username
                    display = (
                        getattr(entity, "title", None)
                        or getattr(entity, "username", None)
                        or chat
                    )
                self._id_to_name[entity_id] = display
                if input_peer_id:
                    self._id_to_name[input_peer_id] = display
                logging.info("Canal registrado: id=%s nombre='%s'", entity_id, display)
                logging.info(
                    "Canal filtro NewMessage: source='%s' entity_type=%s input_peer_id=%s",
                    chat,
                    type(entity).__name__,
                    input_peer_id or "n/a",
                )

        return resolved

    async def _resolve_invite_link(self, chat: str) -> object | None:
        invite_hash = _extract_invite_hash(chat)
        if not invite_hash:
            return None

        try:
            invite = await self._client(CheckChatInviteRequest(invite_hash))
            chat_entity = getattr(invite, "chat", None)
            if chat_entity is not None:
                logging.info(
                    "Chat privado resuelto por invitacion: id=%s titulo='%s'",
                    getattr(chat_entity, "id", 0),
                    getattr(chat_entity, "title", None) or getattr(chat_entity, "username", None) or chat,
                )
                return chat_entity

            updates = await self._client(ImportChatInviteRequest(invite_hash))
            chats = getattr(updates, "chats", None) or []
            if chats:
                chat_entity = chats[0]
                logging.info(
                    "Chat privado unido por invitacion: id=%s titulo='%s'",
                    getattr(chat_entity, "id", 0),
                    getattr(chat_entity, "title", None) or getattr(chat_entity, "username", None) or chat,
                )
                return chat_entity
        except UserAlreadyParticipantError:
            invite = await self._client(CheckChatInviteRequest(invite_hash))
            chat_entity = getattr(invite, "chat", None)
            if chat_entity is not None:
                return chat_entity
        except (InviteHashExpiredError, InviteHashInvalidError) as exc:
            logging.warning("Invite link invalido/expirado para chat %s: %s", chat, exc)
        except Exception as exc:
            logging.warning("No se pudo resolver invitacion privada %s: %s", chat, exc)

        return None

    async def _find_dialog_by_phone(self, phone: str) -> Any | None:
        async for dialog in self._client.iter_dialogs():
            entity = dialog.entity
            entity_phone = _normalize_phone(getattr(entity, "phone", ""))
            if entity_phone and entity_phone == phone:
                return entity
        return None


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if not digits:
        return ""
    return digits


def _extract_invite_hash(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""

    match = _INVITE_LINK_RE.search(text)
    if match:
        return match.group(1)

    if text.startswith("+") and len(text) > 1:
        return text[1:]

    return ""
