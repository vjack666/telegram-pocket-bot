# CHANGELOG

Historial de cambios del proyecto.  
Formato: `[FECHA] Descripción — Archivo(s) afectado(s)`

---

## [2026-04-26] — Sesión de estabilización y limpieza de terminal

### Problema 1 resuelto: Spam de logs de reconexión cada 60 segundos

**Síntoma:** Cada ~60 segundos aparecían en el terminal mensajes repetitivos de:
```
WARNING | reader | desconectado inesperadamente, reintentando...
INFO    | reader | conectado exitosamente a Telegram
```

**Causa raíz:**  
`_keep_alive()` llamaba a `_force_soft_reconnect()` en cada ping independientemente de si era necesario. Esto causaba una desconexión intencional que el loop de `run()` interpretaba como "inesperada" (porque no había mecanismo para distinguirlas).

**Solución aplicada:**

**`src/telegram/reader.py`**

1. **Deshabilitar reconexión periódica forzada:**
   ```python
   # Antes:
   _PERIODIC_SOFT_RECONNECT_SECONDS = 60
   
   # Después:
   _PERIODIC_SOFT_RECONNECT_SECONDS = 0  # 0 = deshabilitado
   ```

2. **Agregar `_planned_disconnect_reason` para distinguir desconexiones:**
   ```python
   # Nuevo campo en __init__:
   self._planned_disconnect_reason: str | None = None
   ```
   
   En `_force_soft_reconnect(reason)`:
   ```python
   self._planned_disconnect_reason = reason
   await self._client.disconnect()
   # Si falla el disconnect:
   self._planned_disconnect_reason = None
   ```
   
   En `run()` al detectar desconexión:
   ```python
   # Antes: siempre WARNING
   log.warning("desconectado inesperadamente")
   
   # Después: diferenciado
   if self._planned_disconnect_reason:
       log.info("desconexion controlada: %s", self._planned_disconnect_reason)
       self._planned_disconnect_reason = None
   else:
       log.warning("desconectado inesperadamente")
   ```

3. **`_keep_alive()` solo reconecta si la constante está activa:**
   ```python
   async def _keep_alive(self) -> None:
       while True:
           await asyncio.sleep(_KEEP_ALIVE_INTERVAL)
           await self._client.get_me()  # ping
           
           if _PERIODIC_SOFT_RECONNECT_SECONDS > 0:
               elapsed = time.monotonic() - self._last_periodic_soft_reconnect_ts
               if elapsed >= _PERIODIC_SOFT_RECONNECT_SECONDS:
                   await self._force_soft_reconnect("periodic_keepalive")
                   self._last_periodic_soft_reconnect_ts = time.monotonic()
   ```

---

### Problema 2 resuelto: Mensajes duplicados en backfill tras cada reconexión

**Síntoma:** Al reconectarse por cualquier razón (VPN, corte breve), el bot volvía a procesar los últimos 15 minutos de mensajes. Aunque la deduplicación los filtraba, generaba logs `"ignorado_por_duplicado"` en cada reconexión.

**Causa raíz:**  
El backfill de mensajes recientes se ejecutaba en cada reconexión sin distinción entre la primera conexión y las subsiguientes.

**Solución aplicada:**

**`src/telegram/reader.py`**

1. **Agregar flag `_backfill_done_once`:**
   ```python
   # Nuevo campo en __init__:
   self._backfill_done_once = False
   ```

2. **Guardar el backfill detrás del flag en `run()`:**
   ```python
   # Antes: siempre ejecutaba backfill tras conectar
   if self._backfill_minutes > 0:
       await self._process_recent_messages(on_message)
   
   # Después: solo en la primera conexión
   if self._backfill_minutes > 0 and not self._backfill_done_once:
       await self._process_recent_messages(on_message)
       self._backfill_done_once = True
   ```

---

### Problema 3 resuelto: Logs mezclados con la línea de cuenta regresiva

**Síntoma:** Durante el countdown de entrada (línea animada con `\r`), cualquier mensaje de `logging` sobreescribía parcialmente la línea del countdown o aparecía mezclado visualmente:
```
\r o LISTO  [ENTRADA] ^ BUY → 00:00:38  ████   INFO | pipeline | señal nueva
```

**Causa raíz:**  
El sistema de `logging` emitía directamente a `sys.stderr`/`sys.stdout` sin saber que la línea actual del terminal estaba ocupada por el countdown animado.

**Solución aplicada:**

**`src/core/console_hub.py`**

1. **Agregar flag `_COUNTDOWN_ACTIVE`:**
   ```python
   _COUNTDOWN_ACTIVE = False  # variable global de módulo
   ```

2. **`print_countdown_line()` setea el flag:**
   ```python
   def print_countdown_line(...) -> None:
       # ... lógica existente de impresión con \r
       global _COUNTDOWN_ACTIVE
       _COUNTDOWN_ACTIVE = True
   ```

3. **`clear_countdown_line()` lo limpia:**
   ```python
   def clear_countdown_line() -> None:
       global _COUNTDOWN_ACTIVE
       print("\r" + " " * _w() + "\r", end="", flush=True)
       _COUNTDOWN_ACTIVE = False
   ```

4. **Nueva función `clear_countdown_line_if_active()`:**
   ```python
   def clear_countdown_line_if_active() -> None:
       if _COUNTDOWN_ACTIVE:
           clear_countdown_line()
   ```

**`src/utils/logger.py`**

5. **Reemplazar `basicConfig` simple con handler personalizado:**
   ```python
   # Antes:
   logging.basicConfig(level=..., format=..., force=True)
   
   # Después:
   class _CleanConsoleStreamHandler(logging.StreamHandler):
       def emit(self, record: logging.LogRecord) -> None:
           try:
               from src.core.console_hub import clear_countdown_line_if_active
               clear_countdown_line_if_active()
           except Exception:
               pass
           super().emit(record)
   
   def setup_logging(level: str = "INFO") -> None:
       logging.basicConfig(
           level=getattr(logging, level.upper(), logging.INFO),
           format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
           handlers=[_CleanConsoleStreamHandler()],
           force=True,
       )
       logging.getLogger("telethon").setLevel(logging.WARNING)
   ```

---

### Verificación final

Todos los cambios validados con:
```bash
python -m compileall src
```
Sin errores de compilación. Sistema confirmado funcionando correctamente por el usuario.

---

## [2026-04-26] — Documentación inicial del proyecto

**Archivos creados en `docs/`:**

| Archivo                | Contenido |
|------------------------|-----------|
| `ARCHITECTURE.md`      | Diagrama de componentes, stack, módulos y flujo de datos |
| `CONFIGURATION.md`     | Todas las variables de entorno con tipos, defaults y ejemplos |
| `SIGNAL_FLOW.md`       | Ciclo de vida completo de una señal, formatos soportados, deduplicación |
| `TELEGRAM_READER.md`   | Referencia técnica del cliente Telegram persistente |
| `CONSOLE_UI.md`        | Sistema de terminal: countdown, colores ANSI, integración con logging |
| `MARTINGALE_MODES.md`  | Modos fixed y calculator con fórmulas y ejemplos |
| `CHANGELOG.md`         | Este archivo |

---

## Historial de versiones previas

El proyecto partió de un prototipo base con:
- Estructura básica Telegram → Parser → PocketOption cliente stub
- Sin ejecución real de órdenes (solo skeleton)

Luego evolucionó hacia:
- Playwright para automatización real del browser
- Engine con martingala y countdown
- Blackbox logging por sesión
- Sistema de terminal mejorado (ANSI, countdown animado)
- Reader Telegram robusto con blindaje de reconexión
