# Documentacion Completa del Sistema

## 1. Objetivo del sistema

Este proyecto automatiza el flujo de senales de Telegram hacia ejecucion en Pocket Option.

Resumen operativo:

1. Lee mensajes de canales/chats de Telegram.
2. Parsea la senal (asset, direccion, expiracion y contexto).
3. Aplica reglas de validacion y riesgo.
4. Ejecuta orden en broker (demo/real) o simula segun configuracion.
5. Monitorea resultado y actualiza estado de sesion/riesgo.
6. Registra telemetria y evidencia operativa.

## 2. Arquitectura general

Punto de entrada:

- main.py

Capas principales:

1. Ingreso de eventos: TelegramSignalReader (Telethon)
2. Pipeline: cola, dedupe, parseo, filtros de tardanza y busy policy
3. Engine: ejecucion de ordenes, countdown, monitoreo de resultado, estado de martingala/sesion
4. Broker client: Playwright sobre Pocket Option
5. Utilidades: logger, blackbox, persistencia de estado

Flujo de alto nivel:

Telegram -> reader -> pipeline -> engine -> pocket client -> resultado -> estado/riesgo -> logs

## 3. Estructura de carpetas (funcional)

- src/config: settings y carga de variables de entorno
- src/core: engine, pipeline, session manager, UI de consola, trackers
- src/telegram: lector MTProto y reconexion
- src/pocket_option: cliente de navegador, assets, feeds de panel
- src/signals: parser de mensajes
- src/utils: logging, blackbox y utilidades operativas
- scripts: utilidades auxiliares (incluye filtro visual)
- docs: referencia funcional y tecnica
- runtime: estado persistido, reportes, blackbox y artefactos de ejecucion

## 4. Modos de ejecucion

### 4.1 Pocket only (sin Telegram)

- APP_ENABLE_TELEGRAM=false
- El sistema abre broker, lee saldo y puede operar manual desde terminal.

### 4.2 Telegram + ejecucion automatica

- APP_ENABLE_TELEGRAM=true
- Requiere TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SOURCE_CHATS.

### 4.3 Dry run vs real

- APP_DRY_RUN=true: no debe enviar ordenes reales.
- POCKET_EXECUTE_ORDERS=true: habilita click real en broker.

Recomendacion: validar siempre en demo antes de real.

## 5. Flujo detallado de una senal

Esta seccion describe el proceso completo desde que entra un mensaje hasta su cierre contable, incluyendo decisiones intermedias y casos especiales.

### 5.1 Pipeline end-to-end por etapas

1. Ingreso de evento (Telegram)
- Telethon recibe NewMessage del chat configurado.
- Reader construye envelope con chat_id, message_id, raw_text, received_at_utc.
- Se encola el envelope para no bloquear el loop del cliente Telegram.

2. Filtro de deduplicacion
- Se consulta cache temporal por clave (chat_id, message_id).
- Si ya existe dentro del TTL: mensaje descartado y registrado como duplicado.
- Si no existe: se marca como visto y continua.

3. Parseo de senal
- SignalParser intenta extraer asset, side, expiracion, hora de entrada y monto.
- Si no logra estructura minima: mensaje no operable (solo log/auditoria).
- Si parsea bien: genera TradingSignal normalizado.

4. Normalizacion y reglas de entrada
- Se aplican overrides globales (asset o side forzado si estan configurados).
- Se valida single_asset_mode (si aplica).
- Se evalua tardanza contra APP_SIGNAL_LATE_TOLERANCE_SECONDS.
- Si la senal ya no es operable por tiempo: se descarta.

5. Control de capacidad (cola y busy policy)
- Si engine esta ocupado, la decision depende de APP_BUSY_POLICY.
- queue: la senal espera turno.
- drop: la senal se descarta para evitar saturacion.
- Si la cola esta llena, se aplica latest-wins segun implementacion del pipeline.

6. Pre-ejecucion y stake
- Engine consulta SessionManager para definir stake actual por estado de deuda/objetivo.
- Aplica limites de seguridad (minimo broker y maximo permitido).
- Registra contexto inicial (balance, estado de sesion, metadata de senal).

7. Gate visual obligatorio (si esta activo)
- VisionGatedExecutionEngine intercepta execute_signal.
- Si VISION_FILTER=false: bypass controlado con reason=filter_disabled.
- Si VISION_FILTER=true:
  - captura screenshot del chart,
  - consulta Vision API,
  - parsea JSON,
  - decide approved true/false.
- Si falla cualquier paso: bloquea orden (fail-safe) y registra razon.

8. Countdown y preparacion broker
- Engine muestra resumen de senal en consola.
- Ejecuta countdown (linea viva MM:SS / HH:MM:SS segun flujo).
- Cambia activo si corresponde y prepara monto en panel.

9. Ejecucion de orden
- Si POCKET_EXECUTE_ORDERS=true: click real BUY/SELL.
- Si no: simulacion (dry run o modo sin click).
- Se registra evento de orden enviada con timestamp.

10. Monitoreo de resultado
- Espera cierre de expiracion + grace period configurado.
- Lee cambios de saldo/snapshot para clasificar WIN, LOSS o UNKNOWN segun evidencia disponible.
- Emite evento de resultado y limpia countdown de consola.

11. Actualizacion de estado de riesgo
- SessionManager actualiza wins/losses, deuda y progreso de sesion.
- Global gale state avanza o resetea segun resultado.
- Daily tracker y equity bands se actualizan si estan activos.

12. Persistencia y auditoria
- Blackbox escribe eventos JSONL en runtime/blackbox.
- Session state se persiste en runtime/session_state.json.
- Si hay gate visual, decisions.csv guarda veredicto por senal.

### 5.2 Timeline operativo de una senal valida (ejemplo)

Ejemplo de entrada:

- Mensaje: GBP/USD OTC, ARRIBA, expiracion 5m, entrada 16:15
- Martingale informado por canal: 16:20 y 16:25

Timeline simplificado:

1. 16:14:10 llega mensaje al reader.
2. 16:14:10 dedupe ok, parse ok, senal encolada.
3. 16:14:11 engine toma turno, calcula stake (por sesion/deuda actual).
4. 16:14:12 gate visual evalua chart (si activo).
5. 16:14:15 inicia countdown y preparacion de broker.
6. 16:15:00 ejecuta entrada.
7. 16:20:00 clasifica resultado de la entrada.
8. 16:20:01 actualiza estado de sesion y define siguiente stake si corresponde.

Nota: aunque el canal publique dos martingalas, la estrategia efectiva depende de la configuracion interna del bot. En el escenario solo_g1 de backtest, gale 2 se ignora por diseno.

### 5.3 Situaciones reales y comportamiento esperado

Situacion A: Mensaje duplicado

- Entrada: mismo message_id repetido por reconexion/backfill.
- Resultado esperado: descartado en dedupe, sin orden, log de duplicado.

Situacion B: Mensaje mal formado

- Entrada: texto sin direccion clara o sin estructura parseable.
- Resultado esperado: parser devuelve None, se registra no_parseable, sin riesgo operativo.

Situacion C: Senal tardia

- Entrada: hora de ejecucion ya vencida por encima de tolerancia.
- Resultado esperado: descarte por tardanza, no se ejecuta orden.

Situacion D: Engine ocupado con busy_policy=queue

- Entrada: llegan varias senales juntas.
- Resultado esperado: se procesan en orden de cola, respetando capacidad.

Situacion E: Engine ocupado con busy_policy=drop

- Entrada: nueva senal mientras otra esta activa.
- Resultado esperado: se descarta la nueva para evitar solapamiento.

Situacion F: Filtro visual activo y rechazo tecnico

- Entrada: vision devuelve approved=false por not_near_fib618 o sr_bias_mismatch.
- Resultado esperado: orden bloqueada, fila registrada en runtime/decisions.csv.

Situacion G: Falla de Vision API

- Entrada: timeout/error API/parse.
- Resultado esperado: fail-safe, no trade, reason api_error/parse_error/vision_filter_error.

Situacion H: Broker no permite accion (selector/UI)

- Entrada: senal valida, gate aprobado, pero falla click o set_amount.
- Resultado esperado: log de error de ejecucion, no se contabiliza como win falso, se mantiene trazabilidad en blackbox.

Situacion I: Resultado ambiguo (UNKNOWN)

- Entrada: no hay evidencia suficiente de cierre/resultado en ventana esperada.
- Resultado esperado: estado conservador, sin marcar win forzado; queda auditoria para revision.

### 5.4 Puntos criticos de control

1. Dedupe correcto para evitar doble entrada por el mismo mensaje.
2. Validacion temporal para no entrar tarde.
3. Gate visual fail-safe para no operar ciego si esta habilitado.
4. Limites de monto para evitar sobreexposicion.
5. Persistencia de estado para recuperacion tras reinicio.
6. Auditoria completa para post-mortem y mejora continua.

## 6. Gestion de riesgo y money management

### 6.1 Session objective (automatico vigente)

Variables principales:

- APP_SESSION_MAX_MESSAGES
- APP_SESSION_TARGET_PROFIT
- APP_SESSION_TARGET_PROFIT_PER_WIN
- APP_SESSION_STOP_LOSS_COUNT

Comportamiento general:

- Define objetivo por sesion y limite de perdidas.
- Ajusta stake segun deuda acumulada y payout.
- Persiste estado en runtime/session_state.json.

### 6.2 Martingale

El sistema conserva referencia de modos legacy fixed/calculator, pero el flujo operativo principal se apoya en SessionManager.

### 6.3 Equity bands (si esta habilitado)

- Escala base operativa segun rangos de capital.
- Puede persistir estado y activar guardas de deposito.

### 6.4 Daily profit tracking (si esta habilitado)

- Meta diaria configurable.
- Modo defensivo al alcanzar objetivo.
- Estado persistido en runtime/daily_profit_state.json.

## 7. Filtro visual obligatorio (Claude Vision)

Integracion actual:

- main.py envuelve SignalEngine con VisionGatedExecutionEngine.
- Antes de cada orden se evalua screenshot del chart con VisionFilterGate.

Reglas base del gate:

1. Si VISION_FILTER=false -> bypass y log reason=filter_disabled.
2. Si VISION_FILTER=true -> solo ejecuta si approved=true.
3. Cualquier error bloquea orden (fail-safe).

Razones fail-safe esperadas:

- api_error
- screenshot_error
- parse_error
- vision_filter_error

Registro:

- runtime/decisions.csv con columnas:
  timestamp, asset, direction, approved, reason, fib_level, sr_zone, equity_before

Variables clave:

- VISION_FILTER
- ANTHROPIC_API_KEY
- ANTHROPIC_MODEL
- VISION_SCREENSHOT_DELAY
- VISION_TIMEOUT

## 8. Operaciones manuales

Cuando APP_MANUAL_OPERATIONS_ENABLED=true:

- El sistema permite registrar operaciones manuales (WIN/LOSS/UNKNOWN).
- Ajusta estado de sesion y gale en base al resultado manual.
- Puede trabajar por deteccion en dos fases usando cambios de saldo y panel live.

Objetivo:

- Integrar operaciones manuales reales del operador al estado del bot para evitar desalineacion.

## 9. Logging, auditoria y trazabilidad

### 9.1 Logging

- setup_logging centraliza formato y nivel.
- Handler limpia countdown activo para no romper la UI de terminal.

### 9.2 Blackbox

- Guarda eventos JSONL en runtime/blackbox.
- Permite auditoria post-mortem y analisis de sesiones.

### 9.3 Persistencia relevante

- signal_reader.session (sesion Telegram)
- .pocket_profile (sesion navegador broker)
- runtime/session_state.json
- runtime/daily_profit_state.json
- runtime/decisions.csv

## 10. Configuracion esencial (.env)

Bloques minimos:

1. General y seguridad
- APP_LOG_LEVEL
- APP_DRY_RUN
- APP_ENABLE_TELEGRAM

2. Telegram
- TELEGRAM_API_ID
- TELEGRAM_API_HASH
- TELEGRAM_SESSION_NAME
- TELEGRAM_SOURCE_CHATS

3. Broker
- POCKET_ACCOUNT_MODE
- POCKET_DEMO_URL
- POCKET_PROFILE_DIR
- POCKET_HEADLESS
- POCKET_EXECUTE_ORDERS
- POCKET_MAX_ORDER_AMOUNT

4. Sesion automatica
- APP_SESSION_MAX_MESSAGES
- APP_SESSION_TARGET_PROFIT
- APP_SESSION_TARGET_PROFIT_PER_WIN
- APP_SESSION_STOP_LOSS_COUNT

5. Filtro visual
- VISION_FILTER
- ANTHROPIC_API_KEY
- ANTHROPIC_MODEL
- VISION_SCREENSHOT_DELAY
- VISION_TIMEOUT

Plantilla recomendada:

- .env.example

## 11. Inicio operativo recomendado

1. Crear/activar venv.
2. pip install -r requirements.txt
3. playwright install chromium
4. Copiar .env.example a .env y completar credenciales.
5. Iniciar con APP_DRY_RUN=true y POCKET_EXECUTE_ORDERS=false.
6. Validar logs, parseo y decisions.csv.
7. Pasar a demo con ejecucion real controlada.
8. Solo despues considerar cuenta real.

## 12. Reportes y backtesting en este workspace

Artefactos disponibles en runtime:

- ejemplo_operaciones_200.csv
- ejemplo_operaciones_200_solo_g1.csv
- ejemplo_backtest_200_solo_g1_resumen.txt
- ejemplo_reporte_200_solo_g1.xlsx

Escenario solo_g1 documentado:

- Se elimina gale 2.
- Todo Win Gale 2 se trata como Loss.
- Se recalcula pnl/equity/rachas y linea de tiempo minuto a minuto en Excel.

## 13. Troubleshooting rapido

1. ImportError por simbolos faltantes
- Causa comun: mezcla de versiones al copiar parcial.
- Solucion: sincronizar src y scripts completos.

2. ModuleNotFoundError de dependencias
- Solucion: instalar faltantes en la venv activa (ejemplo: anthropic).

3. No llegan mensajes Telegram
- Revisar TELEGRAM_SOURCE_CHATS y sesion autorizada.
- Verificar logs del reader y conectividad.

4. Ordenes no se ejecutan
- Revisar APP_DRY_RUN y POCKET_EXECUTE_ORDERS.
- Verificar selectores y estado de sesion en broker.

5. Filtro visual bloquea todo
- Revisar runtime/decisions.csv (reason).
- Ajustar timeout/delay o desactivar temporal con VISION_FILTER=false.

## 14. Seguridad operativa

- Nunca subir .env ni archivos de sesion al repositorio.
- Tratar signal_reader.session como secreto.
- Mantener limites de monto (POCKET_MAX_ORDER_AMOUNT).
- Operar primero en demo.
- Ante fuga de credenciales, rotar inmediatamente.

## 15. Referencias oficiales internas (lectura recomendada)

- README.md
- docs/ARCHITECTURE.md
- docs/CONFIGURATION.md
- docs/SIGNAL_FLOW.md
- docs/MARTINGALE_MODES.md
- docs/MANUAL_OPERATIONS.md
- docs/TELEGRAM_READER.md
- docs/CONSOLE_UI.md
- docs/DAILY_PROFIT_TRACKING_CONFIG.md
- README_VISION_FILTER.md

## 16. Estado actual del sistema (resumen ejecutivo)

- Integracion de gate visual activa en main via wrapper (fail-safe).
- Session objective configurado para objetivo por win y control de riesgo.
- Pipeline y ejecucion estabilizados para operacion continua.
- Backtesting y reportes disponibles en runtime para comparativas.

Esta guia funciona como documento maestro. Para cambios funcionales, actualizar primero este archivo y despues la documentacion tecnica especifica afectada.
