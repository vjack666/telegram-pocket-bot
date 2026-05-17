# Calculadora Trading (Binarias)

Aplicación Flutter para gestionar entradas en operaciones binarias usando una lógica de objetivo por escalones, recuperación tras pérdidas (gale) y control de riesgo por límite del 10%.

## Funcionalidades principales

- Cálculo automático de monto de entrada según objetivo y payout.
- Objetivo automático por incremento o objetivo manual por ciclo.
- Modo multiplicador opcional para escalar la siguiente entrada.
- Regla de control de riesgo por 10% del saldo cuando el saldo supera $50.
- Reinicios automáticos de ciclo según reglas de pérdidas.
- Persistencia de saldo con SharedPreferences.
- Historial de saldo con gráfica de tendencia (fl_chart).
- Botón para copiar el monto exacto de entrada.

## Flujo del sistema

1. El sistema carga el saldo guardado al iniciar.
2. Con saldo, payout e incremento calcula:
	 - Objetivo del ciclo.
	 - Inversión base necesaria para alcanzar ese objetivo.
3. Cada operación se registra con los botones:
	 - `GANÓ`: cierra el ciclo en objetivo y reinicia conteo de pérdidas.
	 - `PERDIÓ`: descuenta inversión, incrementa pérdidas y calcula siguiente monto.
4. Si se activa una condición de riesgo o reset, se recalcula un nuevo ciclo.
5. Cada cambio relevante se agrega al historial para mostrarlo en la gráfica.

## Lógica detallada de cálculo

### 1) Variables de entrada

- `saldoActual`: balance disponible actual.
- `incremento`: salto en dólares para objetivo automático.
- `payout`: porcentaje de retorno convertido a decimal. Ejemplo: 92% -> 0.92.
- `objetivoManual` (opcional): si es mayor que el saldo, reemplaza el objetivo automático.
- `usarMultiplicador`: define si la siguiente entrada usa multiplicador fijo o cálculo por utilidad faltante.

### 2) Cálculo del objetivo

Si hay objetivo manual válido, se usa ese valor.
Si no, objetivo automático:

$$
objetivo = \lfloor saldoActual \rfloor + incremento
$$

Esto fuerza objetivos por escalones enteros.

### 3) Cálculo de inversión base

Primero se calcula utilidad necesaria:

$$
utilidadNecesaria = objetivo - saldoActual
$$

Luego inversión base:

$$
inversionBase = \frac{utilidadNecesaria}{payout}
$$

La inversión actual arranca en la inversión base al inicio de cada ciclo.

### 4) Evento GANÓ

- Ganancia teórica:

$$
ganancia = inversionActual \times payout
$$

- El sistema suma ganancia pero después fija el saldo exactamente al objetivo para cerrar ciclo limpio.
- Limpia objetivo manual (es de un solo uso por ciclo).
- Reinicia pérdidas (`perdidas = 0`).
- Recalcula nuevo objetivo y nueva inversión base para el siguiente ciclo.

### 5) Evento PERDIÓ

- Nuevo saldo:

$$
saldoActual = saldoActual - inversionActual
$$

- Si queda negativo, se corrige a 0.
- Incrementa contador de pérdidas.

#### Regla cuando saldo <= $50

- Si hay 3 pérdidas consecutivas, se hace reset de gale:
	- `perdidas = 0`
	- se recalcula ciclo desde el nuevo saldo.

#### Regla cuando saldo > $50 (regla del 10%)

- Se activa límite:

$$
limite = \lfloor saldoActual \times 0.10 \rfloor
$$

- Se calcula siguiente inversión y si la inversión estimada (redondeada) es mayor o igual al límite, se resetea ciclo por riesgo.

### 6) Cálculo de siguiente inversión tras pérdida

Si `usarMultiplicador = true`:

$$
siguiente = inversionActual \times multiplicador
$$

Si `usarMultiplicador = false`:

$$
siguiente = \frac{objetivo - saldoActual}{payout}
$$

Esta segunda forma busca recuperar y aún cerrar el ciclo en el objetivo actual.

## Persistencia y gráfica

- El saldo se guarda automáticamente en almacenamiento local (`SharedPreferences`).
- El historial agrega puntos cuando cambia el saldo.
- La pestaña Gráfica muestra la evolución del saldo y permite resetear solo la gráfica (sin borrar saldo).

## Prompt sugerido (funcionamiento del sistema)

Usa este prompt para documentar, auditar o explicar la lógica de la app:

```text
Actúa como analista funcional de una calculadora de trading binario.

Quiero que expliques la lógica completa del sistema con detalle técnico y lenguaje claro.
Debes cubrir exactamente estos puntos:

1) Entradas del sistema:
- saldoActual
- payout (convertido de % a decimal)
- incremento
- objetivoManual opcional
- modo multiplicador y multiplicador

2) Lógica del objetivo:
- Si objetivoManual > saldoActual, usar objetivoManual.
- Si no, usar objetivo automático por escalones: floor(saldoActual) + incremento.

3) Lógica de inversión base:
- utilidadNecesaria = objetivo - saldoActual
- inversionBase = utilidadNecesaria / payout
- inversionActual inicia como inversionBase.

4) Evento GANÓ:
- ganancia = inversionActual * payout
- actualizar saldo
- forzar cierre de ciclo en el objetivo
- resetear pérdidas
- limpiar objetivo manual
- recalcular nuevo ciclo.

5) Evento PERDIÓ:
- saldoActual = saldoActual - inversionActual
- evitar saldo negativo
- aumentar contador de pérdidas
- reglas de reset:
	a) saldo <= 50: reset por 3 pérdidas
	b) saldo > 50: activar límite de riesgo del 10% y reset si la siguiente inversión >= límite

6) Cálculo de siguiente inversión tras pérdida:
- Modo multiplicador: inversionActual * multiplicador
- Modo normal: (objetivo - saldoActual) / payout

7) Persistencia y visualización:
- guardado local de saldo
- historial de saldo
- gráfica de tendencia

Incluye fórmulas, ejemplos numéricos simples y casos borde (payout=0, saldo=0, objetivo manual inválido).
```

## Cómo ejecutar

1. Instala Flutter y valida entorno:

```bash
flutter doctor
```

2. Instala dependencias:

```bash
flutter pub get
```

3. Ejecuta la app:

```bash
flutter run
```
