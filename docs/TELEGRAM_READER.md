# TelegramSignalReader — Referencia técnica

Archivo: `src/telegram/reader.py`  
Clase principal: `TelegramSignalReader`

---

## Filosofía de diseño

El `TelegramClient` de Telethon se crea **una sola vez** en `__init__` con `auto_reconnect=True` y reintentos virtualmente infinitos. Nunca se recrea.

Cuando la conexión cae (VPN, corte de red, timeout), el loop en `run()` detecta el error, espera con backoff exponencial y llama de nuevo a `run_until_disconnected()` sobre el **mismo cliente**, reutilizando la sesión guardada en disco (`.session`).

Este diseño evita la necesidad de re-autenticarse y garantiza que el bot sobreviva interrupciones de red sin intervención manual.

---

## Constantes de configuración

Ubicadas al inicio del módulo:

```python
_KEEP_ALIVE_INTERVAL = 60          # segundos entre pings de keep-alive
_PERIODIC_SOFT_RECONNECT_SECONDS = 0  # 0 = deshabilitado (ver nota abajo)
_MAX_RETRY_SECONDS = 30            # techo del backoff entre reconexiones
```

### `_KEEP_ALIVE_INTERVAL`
Cada 60 segundos, `_keep_alive()` hace un ping al servidor de Telegram para mantener la conexión viva en redes que cierran sockets inactivos.

### `_PERIODIC_SOFT_RECONNECT_SECONDS = 0`
**Deshabilitado intencionalmente.**  
Antes, esta constante era 60 y causaba que el bot se desconectara y reconectara cada minuto, generando spam de logs ("desconectado inesperadamente") y mini-interrupciones en la recepción de señales.  
Con valor `0`, la reconexión periódica forzada está desactivada. El keep-alive con ping es suficiente para mantener la conexión estable.

### `_MAX_RETRY_SECONDS`
Techo del backoff exponencial entre intentos de reconexión tras un error inesperado. El delay empieza en ~1s y se duplica hasta este máximo.

---

## Estado interno

```python
self._client: TelegramClient         # Creado una vez, nunca recreado
self._source_chats: list[str]        # Chats/canales configurados
self._backfill_minutes: float        # Minutos de backfill al conectar
self._backfill_limit: int            # Máx mensajes en backfill
self._channel_names: dict[str, str]  # raw_chat → nombre amigable
self._restart_after_signal: bool     # Reiniciar soft tras cada señal
self._reconnect_lock: asyncio.Lock   # Evita reconexiones concurrentes
self._last_forced_reconnect_ts: float
self._planned_disconnect_reason: str | None  # None = desconexión inesperada
self._last_periodic_soft_reconnect_ts: float
self._backfill_done_once: bool       # True tras primer backfill exitoso
self._id_to_name: dict[int, str]     # chat_id → nombre resuelto en runtime
self._dispatch_tasks: set[asyncio.Task]  # Tareas de despacho activas
self._handlers_registered: bool      # True tras registrar handlers Telethon
```

---

## Métodos principales

### `__init__(api_id, api_hash, session_name, source_chats, ...)`
Construye el `TelegramClient` con parámetros de reconexión infinita.  
**No conecta** — solo inicializa el objeto.

### `async run(on_message, shutdown_event)`
Loop blindado principal. Estructura:

```
while not shutdown_event.is_set():
    try:
        await client.connect()
        await _resolve_and_join_chats()
        _register_handlers_once()
        
        if backfill_minutes > 0 and not backfill_done_once:
            await _process_recent_messages(on_message)
            backfill_done_once = True
        
        await client.run_until_disconnected()
        
        # Llegamos aquí si la conexión cayó
        if planned_disconnect_reason:
            log INFO "desconexion controlada: {reason}"
        else:
            log WARNING "desconectado inesperadamente"
        
        await asyncio.sleep(backoff)
        
    except (ConnectionError, OSError, ...):
        log WARNING "error de conexión, reintentando..."
        await asyncio.sleep(backoff)
```

### `async _process_recent_messages(on_message)`
Hace fetch de los últimos `backfill_minutes` de mensajes de cada chat configurado.  
Solo se ejecuta **una vez por proceso** (controlado por `_backfill_done_once`).

**¿Por qué una vez?**  
Si el bot se reconecta por un corte de red, no queremos reprocesar todos los mensajes recientes — ya tienen su `msg_id` en el set de deduplicación, pero generan logs innecesarios y presión en la pipeline.

### `async _force_soft_reconnect(reason: str)`
Desconexión controlada con motivo. Pasos:
1. Setea `_planned_disconnect_reason = reason`
2. Llama a `client.disconnect()`
3. El loop en `run()` detecta la desconexión, lee el motivo y loguea INFO en lugar de WARNING
4. Limpia `_planned_disconnect_reason` tras reconectar

### `async _keep_alive()`
Task en background que corre cada `_KEEP_ALIVE_INTERVAL` segundos.  
Envía un ping al servidor para prevenir timeout de socket.  
Solo llama a `_force_soft_reconnect()` si `_PERIODIC_SOFT_RECONNECT_SECONDS > 0` **y** ha pasado el intervalo.

### `async _resolve_and_join_chats()`
Resuelve cada entrada en `source_chats`:
- `@username` → lookup por username
- `https://t.me/+xxxx` → join por hash de invitación
- ID numérico → uso directo

Popula `_id_to_name` con los nombres resueltos.

### `_register_handlers_once()`
Registra el handler `_on_new_message` de Telethon **solo la primera vez** (flag `_handlers_registered`). Esto evita duplicar eventos en reconexiones.

### `async _on_new_message(event)`
Handler de eventos de Telethon. Construye `TelegramInboundMessage` y lo despacha al pipeline vía `asyncio.Task` (sin bloquear el event loop de Telethon).

---

## Manejo de desconexiones

| Tipo de desconexión       | Origen                         | Log generado |
|---------------------------|--------------------------------|--------------|
| Controlada por el bot     | `_force_soft_reconnect()`      | `INFO: desconexion controlada: {reason}` |
| Inesperada (red/VPN)      | Timeout, ConnectionError, etc. | `WARNING: desconectado inesperadamente` |

La distinción se hace con `_planned_disconnect_reason`:
- `None` → inesperada (WARNING)
- Cualquier string → controlada (INFO)

---

## Backfill — solo una vez

```
Inicio del proceso
    │
    ▼
Primera conexión exitosa
    ├── backfill_done_once = False
    └── Ejecuta _process_recent_messages()
            └── backfill_done_once = True

Reconexión #1 (caída de VPN)
    └── backfill_done_once = True → NO ejecuta backfill

Reconexión #2 (timeout de socket)
    └── backfill_done_once = True → NO ejecuta backfill
```

---

## Reconexión exponencial (backoff)

```python
delay = min(delay * 2, _MAX_RETRY_SECONDS)  # empieza en 1s, techo en 30s
await asyncio.sleep(delay)
```

Esto evita storm de reconexiones cuando el servidor de Telegram está caído temporalmente.

---

## Links de invitación

El reader soporta unirse automáticamente a chats privados con link de invitación:

```env
TELEGRAM_SOURCE_CHATS=https://t.me/+AbCdEfGhIjKlMnOp
```

Si el bot ya es participante, ignora el error `UserAlreadyParticipantError` silenciosamente.  
Si el link expiró o es inválido → log WARNING y salta ese chat.

---

## Apagado limpio

`run()` acepta un `asyncio.Event shutdown_event`.  
Cuando se setea (ej: al recibir SIGINT en main.py), el loop termina tras el ciclo actual.  
El cliente se desconecta limpiamente antes de salir.

---

## Consideraciones de escalado

- El reader está diseñado para **un proceso, múltiples canales**
- Telethon maneja internamente el multiplexado de actualizaciones
- Las tareas de despacho (`_dispatch_tasks`) usan fire-and-forget con cleanup automático para no acumular memoria
- No hay concurrencia en el procesamiento de mensajes — el pipeline consume de la cola secuencialmente
