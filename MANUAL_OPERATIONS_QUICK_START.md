# Guía Rápida: Sistema de Operaciones Manuales

## Activar

En tu `.env` agrega:

```env
APP_MANUAL_OPERATIONS_ENABLED=true
```

## Usar

### Registrar una operación manual mientras el bot corre:

```
Operación manual (BUY) $5 en EURUSD OTC
│
├─ Registras: BUY $5, resultado = WIN
│  └─> GlobalGaleState se resetea (step 0)
│      Masaniello se resetea si está en pérdida
│
└─ Registras: BUY $5, resultado = LOSS
   └─> GlobalGaleState avanza (step 1, 2, ...)
       Masaniello sigue la secuencia
```

### Menú interactivo

En la terminal, verás:

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
```

## Ejemplo real

**Escenario:**
- Masaniello base: $10 por sesión, 6 operaciones, 3 wins necesarios
- Bot ejecuta señal → LOSS de $1.20 (step 0 → 1)
- Ves mejor el mercado → Entras manual con $2.40

**Acciones:**

```
1. Haces clic en BUY manualmente en Pocket Option → $2.40
2. Vela cierra → Ganas $10.92 (balance antes $98.80 → después $112.50)
3. En terminal, opción 1 → Registras operación
   - Activo: EURUSD OTC
   - Tipo: BUY
   - Cantidad: $2.40
   - Balance antes: $98.80
   - Resultado: WIN ✅
   - Balance después: $112.50

4. Sistema:
   ✅ Masaniello reset (step 1 → 0)
   ✅ Historial registrado
   ✅ Próxima señal arranca en ENTRADA (no en Gale 1)
```

## Estados Masaniello tras registro

| Registro | GlobalGaleState | MasanielloSessionState |
|----------|-----------------|----------------------|
| **WIN** | `step=0`, `loss=0` | `wins++` |
| **LOSS** | `step++`, `loss+=amount` | `losses++` |
| **UNKNOWN** | No cambia | No cambia |

## Información del resumen (opción 4)

```
Total operaciones: 2
  ✅ Wins:    1
  ❌ Losses:  1
  ❓ Unknown: 0

Riesgo total: $7.40
Resultado neto: $2.72
```

## Archivos creados

- `src/core/manual_operation_tracker.py` — Lógica de rastreo
- `src/core/manual_operation_cli.py` — Interfaz interactiva
- `docs/MANUAL_OPERATIONS.md` — Documentación completa

## Variables de configuración

```env
# Activar/desactivar
APP_MANUAL_OPERATIONS_ENABLED=true|false
```

