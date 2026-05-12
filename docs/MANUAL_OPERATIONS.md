# Manual Operation Tracking — Sistema de Operaciones Manuales

## Descripción

Sistema que permite registrar operaciones manuales que haces en Pocket Option cuando estás en **pérdida de Masaniello** y quieres intentar una recuperación manual.

**Caso de uso:**
1. Bot ejecuta una señal → **LOSS** en step 1 o 2
2. Ves mejor el mercado y entras **manualmente con mayor stake**
3. Tu entrada gana → Registras **WIN** → Masaniello resetea
4. Tu entrada pierde → Registras **LOSS** → Masaniello continúa secuencia

---

## Configuración en `.env`

```env
# Habilitar sistema de operaciones manuales
APP_MANUAL_OPERATIONS_ENABLED=true

# Puerto para CLI interactivo (opcional)
APP_MANUAL_OPERATIONS_CLI_PORT=9999
```

---

## Cómo usar

### Opción 1: CLI Interactivo (Recomendado)

Mientras el bot corre, presiona **Ctrl+M** en la terminal para abrir el menú interactivo:

```
═════════════════════════════════════════════════════
  📊 REGISTRO DE OPERACIONES MANUALES
═════════════════════════════════════════════════════
1. Registrar operación (WIN/LOSS)
2. Ver última operación
3. Ver historial completo
4. Ver resumen de sesión
5. Salir
─────────────────────────────────────────────────────
Selecciona opción (1-5): 1
```

Luego sigue los pasos:

```
─────────────────────────────────────────────────────
  Registrar Operación Manual
─────────────────────────────────────────────────────
Activo (ej: EURUSD OTC): EURUSD OTC
Tipo de operación (BUY/SELL): BUY
Cantidad (USD): 5
Balance ANTES de operación: 102.50
Resultado de la operación:
  1. WIN
  2. LOSS
  3. UNKNOWN
Selecciona (1-3): 1
Balance DESPUÉS de operación (opcional): 112.50
Notas (opcional): Mejor entrada que la señal anterior

✅ Operación registrada:
   Activo: EURUSD OTC
   Tipo: BUY
   Cantidad: $5.00
   Resultado: WIN
   P&L: +$10.00
   Timestamp: 2026-05-10 14:35:22 UTC
```

### Opción 2: Registrar via API/Código

```python
from src.core.manual_operation_tracker import ManualOperationTracker

# Dentro del engine o contexto con acceso al tracker:
tracker = engine._manual_operation_tracker

if tracker is not None:
    op = tracker.register_manual_operation(
        asset="EURUSD OTC",
        side="BUY",
        amount=5.0,
        balance_before=102.50,
        result="WIN",  # "WIN", "LOSS", o "UNKNOWN"
        balance_after=112.50,
        notes="Entrada manual correcta"
    )
    print(f"Operación registrada: {op}")
```

---

## Cómo funciona internamente

### Detección automática por saldo (sin reloj fijo)

Cuando está activo el `BalanceMonitor`, las operaciones manuales en broker se procesan en **2 fases sin depender de un reloj sincronizado**:

**Fase 1: Apertura detectada (débito de stake) - SE MUESTRA LA CUENTA ATRÁS**
- Cuando el saldo cae significativamente, se detecta como **apertura manual** (estado `PENDING`).
- Sistema **lee el panel del broker** para obtener el countdown real en formato MM:SS (minutos:segundos).
- **Se muestra en log:** `📊 APERTURA MANUAL DETECTADA | reservado=$X | CUENTA ATRÁS ⏱️ M:SS (fuente)`
- **NO se marca `LOSS` en este momento** — el broker reserva/descuenta el stake al enviar la orden, es normal.
- Se captura timestamp exacto de apertura (`opened_at`).

**Fase 2: Cierre detectado (resultado real) - ESPERA A QUE TERMINE EL TIMER**
- Sistema **monitorea continuamente** el panel para detectar cuándo desaparece (operación cerrada).
- **Cascata de detección de cierre:**
  1. **Panel vivo:** Lee el countdown que el broker muestra (MM:SS)
  2. **UI label:** Si no hay timer en panel, parsea configuración (M1 → 60s, M5 → 300s)
  3. **Snapshot null:** Si el panel desaparece, cierre **inmediato** detectado
  4. **Fallback conservador:** Si todo falla, espera 90 segundos
- Cuando se alcanza `expected_close_at + ventana_de_gracia` (5 segundos), se evalúa resultado:
  - Si balance subió → `✅ GANADA` (recompensa cobrada)
  - Si balance bajó → `❌ PERDIDA` (stake perdido)
  - Se muestra en log: `⏸️  CIERRE DETECTADO - Operación manual ✅/❌ | cambio=$X`

**Ventajas del diseño 2-fase sin reloj:**
- ✅ Funciona con **cualquier timeframe** (8s, 60s, M5, M15, etc.) sin ajuste manual
- ✅ No requiere **sincronización perfecta** con broker
- ✅ Tolera **delays de red** (apertura puede verse ~3-5s después del click)
- ✅ **Detecta cierre con precisión** basándose en desaparición del panel
- ✅ **Muestra el countdown en tiempo real** para que veas exactamente cuándo cierra

### 1. Flujo de registro

```
Usuario registra: BUY $5 → WIN
        ↓
ManualOperationTracker.register_manual_operation()
        ↓
┌─ Si WIN:
│  ├─ global_gale_state.record_win()    → resetea paso actual
│  └─ masaniello_session.record_win()   → resetea sesión si completa
│
├─ Si LOSS:
│  ├─ global_gale_state.record_loss()   → incrementa paso
│  └─ masaniello_session.record_loss()  → incrementa contador
│
└─ Si UNKNOWN:
   └─ No modifica estado (usuario investigará)
```

### 2. Estado de Masaniello actualizado

Cuando registras una operación **WIN**:
```
GlobalGaleState:
  current_step = 0          (reseteado)
  accumulated_loss = 0.0    (reseteado)

MasanielloSessionState:
  wins += 1
  session_blocked = False   (desbloqueado si estaba)
```

Cuando registras una operación **LOSS**:
```
GlobalGaleState:
  current_step += 1         (avanza a siguiente gale)
  accumulated_loss += amount

MasanielloSessionState:
  losses += 1
  Si losses >= max_losses → sesión bloqueada
```

### 3. Historial y auditoría

Todas las operaciones se guardan en memoria (y opcionalmente en JSON):

```json
{
  "timestamp": "2026-05-10T14:35:22Z",
  "asset": "EURUSD OTC",
  "side": "BUY",
  "amount": 5.0,
  "result": "WIN",
  "balance_before": 102.50,
  "balance_after": 112.50,
  "notes": "Mejor entrada manual"
}
```

---

## Comportamiento esperado

### Escenario 1: WIN manual recupera pérdida de bot

```
[13:00] Signal bot BUY $2   → LOSS  (step 0 → 1, loss=$2)
[13:05] Manual BUY $4       → WIN   (step 1 → 0, reseteo) ✅
[13:10] Signal bot BUY $2   → puede ganar o perder normalmente
```

**Resultado:** La pérdida de $2 fue recuperada con ganancia neta de $2 ($4 - $2).

### Escenario 2: LOSS manual continúa Masaniello

```
[13:00] Signal bot BUY $2   → LOSS  (step 0 → 1)
[13:05] Manual BUY $4       → LOSS  (step 1 → 2, pérdida total=$6)
[13:10] Signal bot BUY $10  → debe ganar para recuperar $6
```

**Resultado:** Masaniello continúa en step 2. Si bot WIN → ciclo resetea.

### Escenario 3: UNKNOWN no afecta Masaniello

```
[13:00] Signal bot BUY $2   → LOSS  (step 0 → 1)
[13:05] Manual BUY $4       → UNKNOWN (step sigue en 1, sin cambios)
[13:10] Esperas a tener confirmación manual
[13:15] Registras como WIN  (step 1 → 0, reseteo)
```

---

## Ejemplos de logs - Qué esperar en consola

### Ejemplo 1: Trade ganado (M1 = 60 segundos)

```log
[19:12:26] 📊 APERTURA MANUAL DETECTADA | reservado=$1.00 (125.24 → 124.24) | CUENTA ATRÁS ⏱️ 1:00 (trade_panel_countdown)
[19:12:27]    Monitor: snapshot OK, timer=59s
[19:12:28]    Monitor: snapshot OK, timer=58s
[19:12:29]    Monitor: snapshot OK, timer=57s
...
[19:13:26]    Monitor: snapshot NULL → Cierre detectado
[19:13:27] ⏸️  CIERRE DETECTADO - Operación manual ✅ GANADA | cambio=$1.92 (saldo: 124.24 → 126.16)
```

### Ejemplo 2: Trade perdido (M1 = 60 segundos)

```log
[19:07:26] 📊 APERTURA MANUAL DETECTADA | reservado=$1.00 (125.24 → 124.24) | CUENTA ATRÁS ⏱️ 1:00 (trade_panel_countdown)
[19:07:27]    Monitor: snapshot OK, timer=59s
[19:07:28]    Monitor: snapshot OK, timer=58s
...
[19:08:26]    Monitor: snapshot NULL → Cierre detectado
[19:08:27] ⏸️  CIERRE DETECTADO - Operación manual ❌ PERDIDA | cambio=-$1.00 (saldo: 125.24 → 124.24)
```

### Ejemplo 3: Timer muy corto (8 segundos)

```log
[19:15:10] 📊 APERTURA MANUAL DETECTADA | reservado=$1.00 (100.00 → 99.00) | CUENTA ATRÁS ⏱️ 0:08 (trade_panel_countdown)
[19:15:11]    Monitor: snapshot OK, timer=7s
[19:15:12]    Monitor: snapshot OK, timer=6s
[19:15:13]    Monitor: snapshot OK, timer=5s
[19:15:14]    Monitor: snapshot OK, timer=4s
[19:15:15]    Monitor: snapshot OK, timer=3s
[19:15:16]    Monitor: snapshot OK, timer=2s
[19:15:17]    Monitor: snapshot OK, timer=1s
[19:15:18]    Monitor: snapshot NULL → Cierre detectado
[19:15:19] ⏸️  CIERRE DETECTADO - Operación manual ✅ GANADA | cambio=$0.92 (saldo: 99.00 → 99.92)
```

### Ejemplo 4: Timer leído desde UI label (si panel no muestra countdown)

```log
[19:20:10] 📊 APERTURA MANUAL DETECTADA | reservado=$1.00 (150.00 → 149.00) | CUENTA ATRÁS ⏱️ 5:00 (ui_expiry_label)
[19:20:11]    Monitor: Panel timer no disponible, usando UI label (M5 = 300s)
[19:22:50]    Monitor: snapshot NULL → Cierre detectado
[19:22:51] ⏸️  CIERRE DETECTADO - Operación manual ✅ GANADA | cambio=$2.80 (saldo: 149.00 → 151.80)
```

**Nota:** El emoji ⏱️ indica que el sistema **está viendo el ticker en tiempo real** y sabe exactamente cuándo cierra. No es una estimación arbitraria.

---

## Validación en campo: Probe de operación real

Para confirmar que el sistema 2-fase funciona en la práctica, se ejecutó un probe real en **2026-05-11 19:07 UTC** con:
- **Orden:** BUY EUR/USD OTC, $1, M1 expiry
- **Resultado:** LOSS ($1 perdido)

**Timestamps capturados:**
```
Click enviado (to broker):    2026-05-11 19:07:26.723959 UTC
First snapshot (apertura):    2026-05-11 19:07:29.115254 UTC (+2.39s)
Last snapshot (pre-cierre):   2026-05-11 19:08:30.064936 UTC
Cierre detectado (null):      2026-05-11 19:08:32.005141 UTC (+61.28s total)
```

**Observaciones:**
- ✅ Apertura detectada en **2.39 segundos** tras el click (tolerancia de latencia)
- ✅ Cierre detectado en **61.28 segundos** para M1 (esperado ~60s)
- ✅ Panel text en apertura: `"EUR/USD OTC+92%$1+$1.92"` (sin timer en primer snapshot)
- ✅ Timer eventualmente disponible en snapshots posteriores
- ✅ Precisión de timestamps: **milisegundos**, apto para auditoría Masaniello

**Conclusión:** El sistema 2-fase sin reloj funciona con precisión y tolerancia adecuadas para auditoría de operaciones manuales en Masaniello.

---

## Consideraciones

| Aspecto | Comportamiento |
|--------|----------------|
| **Balance iniciado** | Lee del broker, no se modifica automáticamente |
| **Timing de registro** | Puede hacerse DESPUÉS de que cierre la vela |
| **Límite de steps** | Sigue aplicándose `APP_CALC_MAX_STEPS` |
| **Capital operativo** | Si `APP_EQUITY_BANDS_ENABLED=true`, la base se actualiza normalmente |
| **Depósitos externos** | No se detectan automáticamente (solo `equity_deposit_guard_enabled`) |
| **Auditoría** | Historial completo en memoria; puedes exportar via CLI opción 3 |

---

## Comandos rápidos

| Atajo | Función |
|-------|---------|
| `Ctrl+M` | Abre menú interactivo |
| `Opción 1` | Registra nueva operación |
| Opción 4 | Resumem: total de operaciones, wins, losses, P&L |

---

## Ejemplo completo: Recuperación manual en Masaniello

**Configuración:**
```env
APP_MARTINGALE_MODE=masaniello
APP_MASANIELLO_N_OPS=6
APP_MASANIELLO_W_NEEDED=3
APP_MASANIELLO_BASE_BALANCE=10.0
```

**Ejecución:**

```
[14:00] Signal #1: EURUSD BUY $1.20  → LOSS
        Masaniello: step 0→1, accumulated=$1.20

[14:01] 🤔 Ves mala lectura. Entras manual BUY $2.40
        Balance antes: $98.80
        
[14:02] Tu entrada WINS → Balance: $113.52
        ✅ Registras: BUY $2.40 → WIN
        Masaniello: step 1→0, RESETEO ✅
        
[14:03] Signal #2: EURUSD SELL $1.20 → WIN
        Masaniello: completaría sesión si llegara a 3 wins
        
[14:04] Signal #3: EURUSD BUY $1.20  → WIN (3er win)
        Masaniello: Sesión completada ✅
        Capital base aumenta si está en modo macro-gale
```

**Resumen:**
- Recuperaste $1.20 de pérdida + ganancia de $1.20
- El Masaniello continuó funcionando normalmente
- Auditoría completa: ¿cuándo entró manual y por qué?

