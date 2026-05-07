# Configuración — Variables de entorno (.env)

Todas las variables se leen desde el archivo `.env` en la raíz del proyecto.  
Usa `.env.example` como plantilla base.

---

## Telegram

| Variable                        | Tipo     | Defecto                | Descripción |
|---------------------------------|----------|------------------------|-------------|
| `APP_ENABLE_TELEGRAM`           | bool     | `false`                | Activa el cliente Telegram. Si es `false` el bot corre solo con Pocket Option. |
| `TELEGRAM_API_ID`               | int      | *(requerido si activo)*| API ID de la app registrada en my.telegram.org |
| `TELEGRAM_API_HASH`             | str      | *(requerido si activo)*| API Hash de la app |
| `TELEGRAM_SESSION_NAME`         | str      | `signal_reader`        | Nombre del archivo `.session` de Telethon |
| `TELEGRAM_SOURCE_CHATS`         | CSV str  | *(requerido si activo)*| Lista separada por comas de chats/canales a escuchar (ej: `@canal1,@canal2`) |
| `TELEGRAM_BACKFILL_MINUTES`     | float    | `15`                   | Cuántos minutos hacia atrás hacer backfill al conectar (solo primera conexión) |
| `TELEGRAM_BACKFILL_SECONDS`     | int      | *(vacío)*              | Alternativa en segundos a `BACKFILL_MINUTES`. Si se define, tiene prioridad. |
| `TELEGRAM_BACKFILL_LIMIT`       | int      | `40`                   | Máximo de mensajes a procesar en el backfill inicial |
| `APP_TELEGRAM_REALTIME_ONLY`    | bool     | `false`                | Si `true`, ignora el backfill (solo mensajes nuevos desde ahora) |
| `APP_TELEGRAM_RESTART_AFTER_SIGNAL` | bool | `false`               | Si `true`, el reader se reinicia soft después de cada señal procesada |

---

## Aplicación / General

| Variable                        | Tipo     | Defecto     | Descripción |
|---------------------------------|----------|-------------|-------------|
| `APP_DRY_RUN`                   | bool     | `true`      | **Modo seguro.** Si `true`, simula las órdenes sin ejecutarlas. Cambiar a `false` para operar real. |
| `APP_LOG_LEVEL`                 | str      | `INFO`      | Nivel de logging: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `APP_COLOR_OUTPUT`              | bool     | `true`      | Activa colores ANSI en terminal |
| `APP_DEFAULT_AMOUNT`            | float    | `1.0`       | Monto por defecto si la señal no especifica cantidad |
| `APP_DEFAULT_ASSET`             | str      | `EURUSD OTC`| Activo por defecto si la señal no especifica |
| `APP_OVERRIDE_ASSET`            | str      | *(vacío)*   | Si definido, reemplaza el activo de TODA señal. Útil para forzar un par. |
| `APP_OVERRIDE_SIDE`             | str      | *(vacío)*   | Si definido (`BUY`/`SELL`), reemplaza la dirección de toda señal |
| `APP_SINGLE_ASSET_MODE`         | bool     | `false`     | Si `true`, solo procesa señales del activo actualmente seleccionado en la UI |

---

## Timing y zona horaria

| Variable                        | Tipo     | Defecto  | Descripción |
|---------------------------------|----------|----------|-------------|
| `APP_EXPECTED_UTC_OFFSET_HOURS` | int      | `-3`     | Offset UTC esperado de la zona horaria del sistema. Referencia para sincronización. |
| `APP_ENFORCE_UTC_OFFSET`        | bool     | `true`   | Si `true`, verifica que el sistema esté en la zona correcta al iniciar |
| `APP_SIGNAL_TIMEZONE`           | str      | *(del sistema)* | Timezone para interpretar timestamps de señales (ej: `America/Buenos_Aires`) |
| `APP_SIGNAL_LATE_TOLERANCE_SECONDS` | int  | *(ver engine)* | Segundos de tolerancia antes de descartar una señal como tardía |
| `APP_ORDER_RESULT_GRACE_SECONDS`| int      | `15`     | Segundos extra de espera tras el cierre nominal para detectar resultado |

---

## Martingala

| Variable                            | Tipo      | Defecto    | Descripción |
|-------------------------------------|-----------|------------|-------------|
| `APP_MARTINGALE_AMOUNTS`            | CSV float | `2,4,10`   | Montos para cada paso de la secuencia (modo `fixed`) |
| `APP_MARTINGALE_MODE`               | str       | `fixed`    | Modo de cálculo: `fixed` o `calculator` |
| `APP_CALC_PAYOUT_PERCENT`           | float     | `92`       | Payout esperado de Pocket Option en % (para modo `calculator`) |
| `APP_CALC_INCREMENT`                | int       | `2`        | Incremento de unidades por paso (modo `calculator`) |
| `APP_CALC_RULE10_BALANCE_THRESHOLD` | float     | `50`       | Balance umbral para activar Regla 10 en `calculator` |
| `APP_CALC_MAX_STEPS`                | int       | `3`        | Máximo de pasos de martingala permitidos |

Ver [MARTINGALE_MODES.md](MARTINGALE_MODES.md) para descripción completa de cada modo.

---

## Cola y deduplicación

| Variable                        | Tipo  | Defecto   | Descripción |
|---------------------------------|-------|-----------|-------------|
| `APP_PROCESSING_QUEUE_MAXSIZE`  | int   | `500`     | Tamaño máximo de la cola de mensajes. Si se llena aplica política latest-wins. |
| `APP_MESSAGE_DEDUPE_TTL_SECONDS`| int   | *(ver pipeline)* | Tiempo en segundos que se recuerda un `msg_id` para deduplicar |
| `APP_BUSY_POLICY`               | str   | *(ver pipeline)* | Comportamiento cuando el engine está ocupado: `drop`, `queue`, etc. |

---

## Pocket Option — Cuenta

| Variable                        | Tipo   | Defecto                                                     | Descripción |
|---------------------------------|--------|-------------------------------------------------------------|-------------|
| `POCKET_ACCOUNT_MODE`           | str    | `demo`                                                      | Modo de cuenta: `demo` o `real` |
| `POCKET_DEMO_URL`               | str    | `https://pocketoption.com/en/cabinet/demo-quick-high-low/` | URL de la interfaz de trading |
| `POCKET_PROFILE_DIR`            | str    | `.pocket_profile`                                           | Carpeta para persistir la sesión del browser Chromium |
| `POCKET_HEADLESS`               | bool   | `false`                                                     | Correr el browser sin ventana visible. Se recomienda `false` para resolver CAPTCHA. |
| `POCKET_EXECUTE_ORDERS`         | bool   | `false`                                                     | Si `true`, ejecuta clicks reales en el browser para colocar órdenes |
| `POCKET_MAX_ORDER_AMOUNT`       | float  | `5`                                                         | Límite máximo de monto por orden (seguridad) |
| `POCKET_BALANCE_WAIT_SECONDS`   | int    | `240`                                                       | Segundos máximos para esperar que el balance cargue al iniciar |
| `POCKET_KEEP_BROWSER_OPEN`      | bool   | `true`                                                      | Si `true`, el browser se mantiene abierto entre operaciones |

---

## Pocket Option — Selectores CSS

Todos son opcionales. Si se dejan vacíos, el cliente usa selectores por defecto.  
Solo definirlos si la interfaz de Pocket Option cambió o tu cuenta usa un layout diferente.

| Variable                       | Descripción |
|--------------------------------|-------------|
| `POCKET_BALANCE_SELECTOR`      | Selector CSS del elemento que muestra el saldo |
| `POCKET_ASSET_OPEN_SELECTOR`   | Selector del botón para abrir el panel de activos |
| `POCKET_ASSET_SEARCH_SELECTOR` | Selector del campo de búsqueda de activos |
| `POCKET_ASSET_RESULT_SELECTOR` | Selector del resultado en la lista. Puede contener `{asset}` como placeholder. |
| `POCKET_BUY_SELECTOR`          | Selector del botón CALL/BUY |
| `POCKET_SELL_SELECTOR`         | Selector del botón PUT/SELL |
| `POCKET_AMOUNT_SELECTOR`       | Selector del campo de monto |

---

## Nombres de canales (opcional)

Para mostrar nombres amigables en el terminal en lugar del username/ID crudo:

```env
TELEGRAM_CHANNEL_NAMES=@canal_señales:VIP TRADER A,@otro_canal:Señales Pro
```

Formato: `raw_chat:Nombre Amigable` separados por coma.

---

## Ejemplo de .env mínimo funcional (solo Pocket Option, sin Telegram)

```env
APP_DRY_RUN=false
APP_ENABLE_TELEGRAM=false
POCKET_EXECUTE_ORDERS=true
POCKET_ACCOUNT_MODE=demo
POCKET_MAX_ORDER_AMOUNT=5
POCKET_KEEP_BROWSER_OPEN=true
APP_MARTINGALE_AMOUNTS=2,4,10
APP_DEFAULT_ASSET=EURUSD OTC
APP_DEFAULT_AMOUNT=2
```

## Ejemplo de .env completo con Telegram activo

```env
APP_ENABLE_TELEGRAM=true
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_SESSION_NAME=signal_reader
TELEGRAM_SOURCE_CHATS=@mi_canal_señales
TELEGRAM_BACKFILL_MINUTES=15
TELEGRAM_BACKFILL_LIMIT=40

APP_DRY_RUN=false
APP_LOG_LEVEL=INFO
APP_COLOR_OUTPUT=true
APP_DEFAULT_AMOUNT=2
APP_DEFAULT_ASSET=EURUSD OTC
APP_MARTINGALE_AMOUNTS=2,4,10
APP_MARTINGALE_MODE=fixed

POCKET_EXECUTE_ORDERS=true
POCKET_ACCOUNT_MODE=demo
POCKET_MAX_ORDER_AMOUNT=5
POCKET_KEEP_BROWSER_OPEN=true
POCKET_HEADLESS=false

APP_EXPECTED_UTC_OFFSET_HOURS=-3
APP_ENFORCE_UTC_OFFSET=true
```
