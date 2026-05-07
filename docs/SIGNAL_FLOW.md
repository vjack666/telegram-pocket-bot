# Flujo completo de una señal — Telegram → Orden ejecutada

Este documento describe el ciclo de vida de un mensaje de señal desde que llega por Telegram hasta que se registra el resultado de la orden.

---

## Diagrama de secuencia

```
Telegram Canal
     │
     │  Nuevo mensaje de texto
     ▼
TelegramSignalReader
 ├── _on_new_message()            ← handler Telethon
 │    ├── construye TelegramInboundMessage(chat_id, msg_id, text, ts)
 │    └── lanza asyncio.Task → pipeline.enqueue(envelope)
 │
 ▼
SignalPipeline._process_message(envelope)
 ├── [1] Deduplicación
 │    └── ¿msg_id ya visto en TTL ventana? → log "ignorado_por_duplicado" → FIN
 │
 ├── [2] Parseo
 │    └── SignalParser.parse(envelope.text)
 │         ├── éxito → TradingSignal(asset, side, expiry_sec, amount)
 │         └── fallo → log "no_parseable" → FIN
 │
 ├── [3] Override / filtros
 │    ├── APP_OVERRIDE_ASSET   → reemplaza signal.asset
 │    ├── APP_OVERRIDE_SIDE    → reemplaza signal.side
 │    └── APP_SINGLE_ASSET_MODE → ¿asset coincide con activo activo? si no → FIN
 │
 ├── [4] Check tardío
 │    └── ¿timestamp señal + tolerancia < ahora? → log "señal_tardía" → FIN
 │
 └── [5] Despacho al engine
      └── engine.execute_signal(signal)
              │
              ▼
          SignalEngine._run_martingale_flow(signal)
           │
           ├── [A] Lee balance inicial (referencia)
           ├── [B] Determina monto del paso actual (fixed o calculator)
           │
           ├── [C] _run_countdown_and_prepare(signal, amount)
           │    ├── Muestra panel resumen de señal (console_hub)
           │    ├── Espera hasta T-30s para iniciar preparación
           │    ├── Inicia línea de countdown en terminal (\r)
           │    ├── T-30s: switch_asset() → cambia activo en Pocket Option
           │    └── T-0.2s: termina preparación
           │
           ├── [D] client.place_order(side, amount)
           │    ├── setea monto en UI
           │    ├── hace click en BUY/SELL
           │    └── retorna order_id o None (dry_run)
           │
           ├── [E] Muestra evento "ENTRADA ENVIADA" en terminal
           │
           ├── [F] _monitor_order_result_until_close(signal, order_id)
           │    ├── Espera hasta que expire la vela (expiry_seconds)
           │    ├── Lee balance final (+ grace period)
           │    ├── Compara balance antes/después → WIN / LOSS / UNKNOWN
           │    └── Muestra evento resultado en terminal
           │
           └── [G] Actualiza global_gale_state
                ├── WIN  → resetea contador, limpiar estado
                └── LOSS → incrementa paso, acumula pérdida para próxima señal
```

---

## Objetos de datos

### `TelegramInboundMessage`
```python
@dataclass
class TelegramInboundMessage:
    chat_id: int          # ID numérico del chat/canal
    message_id: int       # ID único del mensaje en ese chat
    text: str             # Texto completo del mensaje
    timestamp: datetime   # UTC timestamp del mensaje
    chat_username: str    # @username o "" si es privado
```

### `TradingSignal`
```python
@dataclass
class TradingSignal:
    asset: str            # Ej: "EURUSD OTC"
    side: str             # "BUY" o "SELL"
    expiry_seconds: int   # Duración de la vela en segundos (ej: 300 = 5M)
    amount: float         # Monto en USD de la señal (puede ser sobreescrito por martingala)
    entry_time: datetime  # Momento de entrada deseado (UTC)
    source_chat: str      # Canal de origen
    message_id: int       # ID del mensaje original
```

---

## Formatos de señal soportados

El `SignalParser` reconoce múltiples formatos de texto:

```
EURUSD SELL 5M 10
BUY GBPUSD 1m amount 2
XAUUSD PUT 3M
EURUSD OTC CALL 5 minutes
EUR/USD VENDER 5M $5
```

**Reglas del parser:**
- El activo puede ir antes o después de la dirección
- `BUY` / `CALL` / `UP` → side=BUY
- `SELL` / `PUT` / `DOWN` / `VENDER` → side=SELL
- Expiración en minutos: `1M`, `3m`, `5 minutes`, etc.
- Monto: número después de `amount`, `$`, o al final de línea
- Si no hay monto → usa `APP_DEFAULT_AMOUNT`

---

## Deduplicación

- Cada mensaje tiene un `(chat_id, message_id)` único
- La pipeline mantiene un set con TTL (configurable via `APP_MESSAGE_DEDUPE_TTL_SECONDS`)
- Si el mismo `msg_id` llega dos veces (backfill + realtime, o network glitch) → segundo mensaje ignorado silenciosamente
- El backfill solo corre **una vez** al inicio (`_backfill_done_once = True` en reader)

---

## Gestión de tardanza

La señal tiene un `entry_time` parseado del mensaje (o el timestamp de recepción como fallback).

| Tiempo restante         | Acción |
|-------------------------|--------|
| > 30 segundos           | Espera normal, countdown completo |
| 5–30 segundos           | Entrada rápida, salta preparación |
| < 5 segundos            | Señal descartada como "muy tardía" |
| Negativo (ya pasó)      | Señal descartada |

La tolerancia es configurable con `APP_SIGNAL_LATE_TOLERANCE_SECONDS`.

---

## Política de cola llena (`busy_policy`)

Si el engine está ejecutando una señal cuando llega otra:

| Política  | Comportamiento |
|-----------|----------------|
| `drop`    | La nueva señal se descarta con log WARNING |
| `queue`   | Se encola hasta que el engine esté libre |

La cola tiene límite `APP_PROCESSING_QUEUE_MAXSIZE`. Si se desborda, aplica **latest-wins**: descarta el mensaje más viejo para dar prioridad a los nuevos.

---

## Blackbox / auditoría

Cada evento del flujo se registra en `runtime/blackbox/blackbox_<fecha>_<pid>.jsonl`:

```json
{"ts": "2026-04-26T12:44:21Z", "event": "signal_received", "asset": "EURUSD OTC", "side": "BUY", "expiry": 300}
{"ts": "2026-04-26T12:44:22Z", "event": "order_placed", "amount": 2.0, "order_id": "abc123"}
{"ts": "2026-04-26T12:49:22Z", "event": "order_result", "result": "WIN", "profit": 1.84}
```

Cada ejecución del bot genera un nuevo archivo con PID único para no mezclar runs.
