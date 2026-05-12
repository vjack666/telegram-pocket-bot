# Modos de Martingala — Referencia técnica

Estado actual:
- Este documento describe modos legacy (fixed/calculator).
- El flujo automático principal usa SessionManager (objetivo por sesión: 2 wins, stop loss 3 losses, máximo 6 mensajes).
- Para configuración operativa vigente, ver CONFIGURATION.md.

Configurado via `APP_MARTINGALE_MODE` en `.env`.

---

## Concepto general

La martingala es una estrategia de gestión de riesgo donde, tras una pérdida, el monto de la siguiente operación se incrementa para recuperar lo perdido (y potencialmente obtener ganancia neta).

**El sistema soporta dos modos:**

| Modo         | Variable                  | Descripción |
|--------------|---------------------------|-------------|
| `fixed`      | `APP_MARTINGALE_MODE=fixed`       | Montos predefinidos en lista fija |
| `calculator` | `APP_MARTINGALE_MODE=calculator`  | Cálculo automático basado en balance y payout |

---

## Estado global de martingala

El estado de la martingala es **global por proceso** — no por señal individual.

Esto significa: si la señal A pierde en su paso 0 (ENTRADA), la siguiente señal B que llegue empezará en el paso 1 (Martingala 1) en lugar de paso 0.

```
Señal A → step 0 → LOSS
Señal B → step 1 → WIN  ← recupera A + B
Señal C → step 0  ← ciclo reseteado
```

**`global_gale_state`** mantiene:
- `current_step`: paso actual (0 = Entrada, 1 = M1, 2 = M2...)
- `accumulated_loss`: pérdida total acumulada del ciclo actual
- Historial para el modo `calculator`

---

## Modo `fixed`

### Configuración
```env
APP_MARTINGALE_MODE=fixed
APP_MARTINGALE_AMOUNTS=2,4,10
```

### Comportamiento
Los montos son fijos y se asignan directamente por posición:

| Paso       | `APP_MARTINGALE_AMOUNTS=2,4,10` | Monto usado |
|------------|----------------------------------|-------------|
| Entrada    | índice 0                         | $2.00       |
| Martingala 1 | índice 1                       | $4.00       |
| Martingala 2 | índice 2                       | $10.00      |

Si el paso supera la cantidad de valores definidos → usa el último valor.

### Cuándo resetear el ciclo
- **WIN** en cualquier paso → `current_step = 0`, `accumulated_loss = 0`
- **UNKNOWN** (no se pudo determinar resultado) → mantiene el paso actual

### Límite de pasos
`APP_CALC_MAX_STEPS` también aplica en modo fixed — si se supera, el bot no abre más operaciones en el ciclo hasta que llegue una nueva señal que fuerce reset.

---

## Modo `calculator`

### Configuración
```env
APP_MARTINGALE_MODE=calculator
APP_CALC_PAYOUT_PERCENT=92
APP_CALC_INCREMENT=2
APP_CALC_RULE10_BALANCE_THRESHOLD=50
APP_CALC_MAX_STEPS=3
```

### Lógica de cálculo

El monto de cada paso se calcula para **recuperar la pérdida acumulada + obtener ganancia neta** según el payout de Pocket Option.

#### Fórmula base

$$\text{monto}_n = \frac{\text{perdida\_acumulada} + \text{ganancia\_objetivo}}{payout}$$

Donde:
- `payout` = `APP_CALC_PAYOUT_PERCENT / 100` (ej: 0.92)
- `ganancia_objetivo` se incrementa en `APP_CALC_INCREMENT` unidades por paso

#### Ejemplo con payout 92% e incremento 2:

| Paso | Perdida acum. | Ganancia obj. | Monto calculado |
|------|---------------|---------------|-----------------|
| 0 (ENTRADA)   | $0    | $2    | $2.17   |
| 1 (Marti 1)   | $2.17 | $4    | $6.71   |
| 2 (Marti 2)   | $8.88 | $6    | $16.18  |

**La posición 0 siempre usa `APP_CALC_INCREMENT` como ganancia objetivo base.**

### Regla 10 (`calc_rule10_balance_threshold`)

Si el balance actual es **menor** que `APP_CALC_RULE10_BALANCE_THRESHOLD`:
- Se activa modo conservador
- El monto calculado se limita al 10% del balance disponible
- Previene sobreexposición cuando el saldo está bajo

```python
if balance < rule10_threshold:
    monto = min(monto_calculado, balance * 0.10)
```

### `APP_CALC_MAX_STEPS`
Número máximo de pasos de martingala (sin contar la entrada).  
Si el ciclo llega a este límite sin WIN → ciclo finalizado, reset para la próxima señal.

---

## Límite de exposición por balance

Independiente del modo, el engine aplica un límite de seguridad:

```python
_max_operation_balance_ratio = 0.10  # 10% del balance máximo
```

Si el monto calculado supera el 10% del balance actual → se recorta al 10%.  
Esto opera **además** de `POCKET_MAX_ORDER_AMOUNT`.

El monto efectivo final es:
```python
monto_final = min(monto_calculado, POCKET_MAX_ORDER_AMOUNT, balance * 0.10)
```

---

## Comparación de modos

| Característica                  | `fixed`              | `calculator`          |
|---------------------------------|----------------------|----------------------|
| Montos predecibles              | ✅ Siempre           | ❌ Varía por contexto |
| Recuperación matemática         | ❌ No garantizada    | ✅ Si el payout coincide |
| Requiere saldo preciso          | ❌ No               | ✅ Sí                 |
| Configuración simple            | ✅ Una lista         | ❌ Varios parámetros  |
| Adecuado para cuentas demo      | ✅ Ideal para pruebas| ✅                    |

---

## Recomendaciones

- **Para empezar / modo demo:** usar `fixed` con montos bajos (`2,4,10`)
- **Para optimización avanzada:** usar `calculator` con payout real de la plataforma (verificar en Pocket Option para el activo específico)
- Siempre verificar que `POCKET_MAX_ORDER_AMOUNT` actúe como techo de seguridad
- El payout de Pocket Option varía por activo, horario y tipo de cuenta — ajustar `APP_CALC_PAYOUT_PERCENT` según el activo principal

---

## Verificación del estado de martingala

El estado actual siempre es visible en el panel del terminal cuando llega una señal. El paso actual y los montos calculados se muestran en `print_signal_summary()`.

Los eventos de resultado (WIN/LOSS) se registran en `runtime/blackbox/` para auditoría completa del ciclo.
