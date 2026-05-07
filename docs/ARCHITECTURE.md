# Arquitectura del sistema — Telegram → Pocket Option Bot

## Resumen

Bot de trading automatizado que escucha señales desde canales de Telegram y ejecuta órdenes en Pocket Option (cuenta demo o real) usando automatización de navegador con Playwright.

---

## Stack tecnológico

| Componente     | Tecnología                    | Versión  |
|----------------|-------------------------------|----------|
| Runtime        | Python                        | 3.11+    |
| Telegram       | Telethon (MTProto client)     | 1.36.0   |
| Browser        | Playwright (Chromium)         | 1.52.0   |
| Config         | python-dotenv                 | 1.0.1    |
| Concurrencia   | asyncio (event loop único)    | stdlib   |
| Logging        | logging                       | stdlib   |

---

## Diagrama de componentes

```
┌─────────────────────────────────────────────────────────┐
│                        main.py                          │
│   Punto de entrada. Inicializa todos los componentes.   │
│   Ejecuta el event loop asyncio.                        │
└──────────┬──────────────────────────────────────────────┘
           │
     ┌─────┴──────────┐
     │                │
     ▼                ▼
┌──────────────┐  ┌──────────────────────┐
│  Telegram    │  │    PocketOption       │
│  Reader      │  │    BaseClient         │
│              │  │  (Playwright browser)│
│ TelegramClient│  │                      │
│ Telethon MTProto│  │ Sesión persistida en │
│ auto_reconnect  │  │ .pocket_profile/    │
└──────┬───────┘  └──────────┬───────────┘
       │ TelegramInboundMessage│ balance, click, switch_asset
       ▼                      │
┌──────────────────┐          │
│   Pipeline       │          │
│                  │          │
│ - Deduplicación  │          │
│ - Queue (asyncio)│          │
│ - Parser         │          │
│ - Dispatch       │          │
└──────┬───────────┘          │
       │ TradingSignal         │
       ▼                      │
┌──────────────────────────────┤
│        SignalEngine          │
│                              │
│ - Martingala                 │
│ - Countdown + timekeeping    │
│ - Envío de orden             │
│ - Monitoreo de resultado     │
│ - Manejo de estado global    │
└──────────────────────────────┘
       │ (terminal output)
       ▼
┌──────────────────┐
│   console_hub    │
│                  │
│ - Panel señal    │
│ - Línea cuenta   │
│   regresiva (CR) │
│ - Eventos orden  │
└──────────────────┘
```

---

## Módulos y responsabilidades

### `main.py`
- Carga `AppSettings` desde `.env`
- Instancia todos los componentes una sola vez
- Arranca el event loop con `asyncio.run()`
- Registra señal `SIGINT` para shutdown limpio

### `src/config/settings.py`
- Clase `AppSettings` (dataclass frozen)
- Lee todas las variables de entorno
- Validación de requeridos cuando `APP_ENABLE_TELEGRAM=true`

### `src/telegram/reader.py`
- `TelegramSignalReader` — cliente MTProto persistente
- El `TelegramClient` se crea **una sola vez** en `__init__`
- Loop blindado en `run()` que sobrevive desconexiones
- Backfill de mensajes recientes al conectar (solo la primera vez)
- Keep-alive mediante ping periódico
- Ver [TELEGRAM_READER.md](TELEGRAM_READER.md) para detalles completos

### `src/core/pipeline.py`
- `SignalPipeline` — consume mensajes Telegram, deduplica, parsea y despacha al engine
- `MessageQueue` — cola asyncio con política latest-wins cuando está llena
- Deduplicación por `msg_id` con TTL configurable

### `src/core/engine.py`
- `SignalEngine` — ejecuta señales con lógica de martingala
- Serializa todas las operaciones del broker con `asyncio.Lock` (`_broker_lock`)
- Maneja countdown hasta la entrada, envío de orden y monitoreo del resultado
- Ver [SIGNAL_FLOW.md](SIGNAL_FLOW.md) y [MARTINGALE_MODES.md](MARTINGALE_MODES.md)

### `src/core/models.py`
- `TradingSignal` — dataclass con asset, side, expiry, amount, timestamp

### `src/core/console_hub.py`
- Toda la renderización del terminal
- ANSI colors via clase `C`
- Línea de cuenta regresiva con carriage-return (`\r`)
- Flag `_COUNTDOWN_ACTIVE` para coordinación con el logger
- Ver [CONSOLE_UI.md](CONSOLE_UI.md) para detalles

### `src/signals/parser.py`
- `SignalParser` — interpreta texto libre de mensajes Telegram
- Soporta múltiples formatos (ver README principal)

### `src/pocket_option/client.py`
- `PocketOptionBaseClient` — abstracción del browser Playwright
- Mantiene sesión en `POCKET_PROFILE_DIR` (Chromium persistent context)
- Operaciones: `get_balance()`, `switch_asset()`, `place_order()`

### `src/pocket_option/assets.py`
- Normalización y canonicalización de nombres de activos
- `canonicalize_pocket_asset()` — mapea variantes a nombre oficial PO
- `normalize_asset_for_compare()` — limpia para comparación

### `src/pocket_option/trade_panel_feed.py`
- `LiveTradeSnapshot` — lee precio y estado del panel de trading en tiempo real

### `src/pocket_option/candle_feed.py`
- Feed de velas websocket (referencia de mercado)

### `src/utils/logger.py`
- `setup_logging()` — configura logging global
- `_CleanConsoleStreamHandler` — limpia la línea de countdown antes de cada log
- Ver [CONSOLE_UI.md](CONSOLE_UI.md)

### `src/utils/blackbox.py`
- Registro de todos los eventos a JSONL en `runtime/blackbox/`
- Cada ejecución genera un archivo con timestamp y PID

---

## Flujo de datos principal

```
Telegram Canal
    │  (mensaje texto)
    ▼
TelegramSignalReader.run()
    │  TelegramInboundMessage(chat_id, message_id, text, timestamp)
    ▼
SignalPipeline._process_message()
    ├── dedupe check (mensaje ya visto? → ignorar)
    ├── SignalParser.parse(text)
    │       └── TradingSignal(asset, side, expiry_seconds, amount)
    └── engine.execute_signal(signal)
            ├── calcular monto (fixed o calculator)
            ├── _run_countdown_and_prepare()  → countdown en terminal
            ├── client.switch_asset(asset)
            ├── client.place_order(side, amount)
            └── _monitor_order_result_until_close()
                    └── actualiza global_gale_state (win/loss)
```

---

## Concurrencia

- **Un solo event loop asyncio** — sin threads salvo el proceso Playwright
- **`_broker_lock`** (asyncio.Lock en engine) — serializa todas las operaciones de browser; garantiza que solo un orden se ejecute a la vez
- **`_reconnect_lock`** (asyncio.Lock en reader) — evita reconexiones concurrentes al Telegram
- **`_dispatch_tasks`** (set de asyncio.Task) — las tareas de despacho al pipeline se ejecutan sin bloquear el handler de Telethon

---

## Persistencia

| Qué                      | Dónde                        |
|--------------------------|------------------------------|
| Sesión Telegram          | `signal_reader.session`      |
| Perfil browser Chromium  | `.pocket_profile/`           |
| Logs de ejecución (JSONL)| `runtime/blackbox/`          |
| Resumen operaciones      | `runtime/resumen_operaciones.md` |

---

## Notas de seguridad

- Credenciales Telegram (`API_ID`, `API_HASH`) solo en `.env`, nunca en código
- `.env` **no debe** commitearse a git (agregar a `.gitignore`)
- La sesión `.session` equivale a credenciales — tratarla como secreto
- `APP_DRY_RUN=true` (defecto) previene órdenes reales accidentales
