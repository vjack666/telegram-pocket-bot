# Consola / Terminal UI — Referencia técnica

Archivo: `src/core/console_hub.py`  
Logger: `src/utils/logger.py`

---

## Responsabilidad

`console_hub` centraliza **toda** la renderización del terminal:
- Panel de señal recibida (`print_signal_summary`)
- Línea de cuenta regresiva en tiempo real (`print_countdown_line`)
- Eventos de orden (`print_order_event`)
- Limpieza de pantalla y de la línea de countdown

`logger.py` asegura que los mensajes de `logging` no interfieran con la línea de countdown.

---

## Colores ANSI — clase `C`

```python
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    CYAN    = "\033[96m"
    WHITE   = "\033[97m"
    GREY    = "\033[37m"
    YELLOW  = "\033[93m"
    GREEN   = "\033[92m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
```

Función de pintura:
```python
def _paint(text: str, *codes: str) -> str:
    return "".join(codes) + text + C.RESET
```

Para deshabilitar colores en entornos que no los soportan, pasar `color_output=False` a las funciones de impresión.

---

## Línea de cuenta regresiva

### Mecanismo de carriage-return (`\r`)

La cuenta regresiva usa `\r` (retorno de carro) para **sobreescribir la misma línea** del terminal en lugar de avanzar a una nueva:

```python
print(f"\r{línea}", end="", flush=True)
```

Esto crea la ilusión de una cuenta regresiva animada sin contaminar el scroll del terminal.

**Problema que resuelve:** Si el sistema de `logging` emite un mensaje mientras la línea de countdown está activa, el texto del log se mezclaría visualmente con la cuenta regresiva.

### Flag `_COUNTDOWN_ACTIVE`

```python
_COUNTDOWN_ACTIVE = False  # variable global de módulo
```

| Estado | Significado |
|--------|-------------|
| `False` | No hay línea de countdown activa en el terminal |
| `True`  | Hay una línea de countdown que ocupa la línea actual |

### `print_countdown_line()`
- Imprime la línea con `\r`
- Setea `_COUNTDOWN_ACTIVE = True`

### `clear_countdown_line()`
- Sobreescribe la línea con espacios en blanco: `\r` + `" " * ancho` + `\r`
- Setea `_COUNTDOWN_ACTIVE = False`

### `clear_countdown_line_if_active()`
```python
def clear_countdown_line_if_active() -> None:
    if _COUNTDOWN_ACTIVE:
        clear_countdown_line()
```
- Solo limpia si hay una línea activa
- Llamada automáticamente por `_CleanConsoleStreamHandler` antes de cada log

---

## Funciones públicas

### `print_signal_summary(asset, side, expiry_minutes, martingale_mode, amounts, schedule_labels, color_output)`

Muestra el panel completo de una señal recibida:
```
══════════════════════════════════════════════════════════
  BOT POCKET OPTION - SENAL RECIBIDA
──────────────────────────────────────────────────────────

  Par:            EURUSD OTC   ^ BUY
  Expiracion:     5 min
  Modo martingala: fixed
  Montos:         $2.00  ->  $4.00  ->  $10.00

  Horarios:
  Entrada:          14:30:00
  Martingala 1:     14:35:00
  Martingala 2:     14:40:00
──────────────────────────────────────────────────────────
```

Llama a `clear_screen()` internamente para limpiar el terminal antes de mostrar.

### `print_countdown_line(hh, mm, ss, side, state, color_output)`

Renderiza la línea animada de countdown:
```
\r o LISTO  [ENTRADA]  ^ BUY  →  00:00:42  ████████████░░░░░░  14:30:00
```

La barra de progreso se calcula en base al tiempo restante vs. total de la vela.

Setea `_COUNTDOWN_ACTIVE = True`.

### `print_order_event(event_type, asset, side, amount, result, color_output)`

Muestra un evento puntual en una línea nueva:
```
  [ENTRADA ENVIADA]  EURUSD OTC  ^ BUY  $2.00
  [RESULTADO]  EURUSD OTC  WIN  +$1.84
```

Llama a `clear_countdown_line()` antes de imprimir para no mezclar con el countdown.

### `clear_screen()`
Ejecuta `cls` (Windows) o `clear` (Unix/Mac).

### `clear_countdown_line()`
Limpia la línea de countdown y setea `_COUNTDOWN_ACTIVE = False`.

### `clear_countdown_line_if_active()`
Limpia solo si hay countdown activo. Punto de entrada seguro para el handler de logging.

---

## Integración con el sistema de logging

### `_CleanConsoleStreamHandler` en `src/utils/logger.py`

```python
class _CleanConsoleStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            from src.core.console_hub import clear_countdown_line_if_active
            clear_countdown_line_if_active()
        except Exception:
            pass
        super().emit(record)
```

**Flujo:**
1. Cualquier `logging.info()`, `logging.warning()`, etc. pasa por este handler
2. Antes de emitir, verifica si hay countdown activo
3. Si sí → limpia la línea (`\r` + espacios + `\r`)
4. Luego emite el log normalmente en una línea nueva limpia
5. El engine reimprimirá la línea de countdown en el siguiente tick

**Resultado visual:**
```
# Sin el handler (bug antiguo):
\r o LISTO  [ENTRADA] ^ BUY → 00:00:38  ████   INFO | señal_recibida
                                                ↑ mezclado

# Con el handler (comportamiento correcto):
2026-04-26 14:29:22 | INFO | pipeline | señal_recibida
\r o LISTO  [ENTRADA] ^ BUY → 00:00:38  ████████████░░░░
```

### Configuración del logger

```python
def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[_CleanConsoleStreamHandler()],
        force=True,
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
```

- `force=True` — reemplaza cualquier configuración previa de logging
- `telethon` logger en WARNING — silencia el chatter de conexión de Telethon (pings, actualizaciones de estado de red, etc.) que no aportan información al usuario

---

## Ancho de terminal adaptivo

```python
def _w() -> int:
    return max(80, shutil.get_terminal_size((80, 24)).columns)
```

- Detecta el ancho real del terminal en tiempo de ejecución
- Mínimo garantizado: 80 columnas
- Garantiza que `clear_countdown_line()` limpie toda la línea sin importar el tamaño de la ventana

---

## Cómo extender sin romper el patrón `\r`

1. **Cualquier `print()` nuevo debe llamar primero a `clear_countdown_line()`** para no mezclar con el countdown
2. **No usar `print()` dentro de funciones llamadas por el engine durante el countdown** — pasar por `logging` en su lugar, que el handler limpie automáticamente
3. Si agregas una nueva función de output, sigue el patrón:
   ```python
   def print_mi_evento(...) -> None:
       clear_countdown_line()   # primero limpiar
       print(f"  [MI EVENTO]  {datos}")
   ```
4. Nunca setear `_COUNTDOWN_ACTIVE` manualmente desde fuera de `console_hub`; solo usar las funciones públicas

---

## Desactivar colores

Cuando `APP_COLOR_OUTPUT=false`, todas las funciones aceptan `color_output=False`:
- Se omiten los códigos ANSI
- Los textos usan separadores ASCII simples (`=`, `-`)
- Compatible con terminales sin soporte ANSI (pipelines CI, logs a archivo)
