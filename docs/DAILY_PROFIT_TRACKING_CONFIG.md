
════════════════════════════════════════════════════════════════════════════════════════
PASO 1: ACTIVAR EN .env
════════════════════════════════════════════════════════════════════════════════════════

Agrega estas líneas a tu .env:

    # ── Daily Profit Tracking ──────────────────────────────────────────────
    APP_DAILY_PROFIT_TRACKING_ENABLED=true
    APP_DAILY_PROFIT_TARGET=60.0
    APP_DAILY_PROFIT_DEFENSIVE_MODE=true
    APP_DAILY_PROFIT_STATE_PATH=runtime/daily_profit_state.json

EXPLICACIÓN DE PARÁMETROS:

    APP_DAILY_PROFIT_TRACKING_ENABLED
    ├─ true: Activa el tracking de ganancias diarias
    └─ false: Desactiva (default)

    APP_DAILY_PROFIT_TARGET=60.0
    ├─ Meta diaria en dinero ($)
    ├─ Recomendado: 60 (tu valor actual)
    ├─ Ajustar según capital y riesgo
    └─ Se reinicia cada día a medianoche UTC

    APP_DAILY_PROFIT_DEFENSIVE_MODE=true
    ├─ true: Cambia a modo defensivo cuando alcanza meta
    ├─ Modo defensivo reduce riesgo por operación
    └─ false: Solo trackea, no cambia riesgo

    APP_DAILY_PROFIT_STATE_PATH
    ├─ Donde guardar estado del día actual
    ├─ Se crea automáticamente en runtime/
    └─ Persistente: recuperable si reinicia el bot

════════════════════════════════════════════════════════════════════════════════════════
PASO 2: VERIFICAR LOGS
════════════════════════════════════════════════════════════════════════════════════════

Cuando arranca, verás:

    [DailyProfit] ACTIVO | meta_diaria=$60.00 | modo_defensivo=true | persist=true

Durante las operaciones:

    [Daily Meta] Progreso: $15.30 / $60.00 (25.5%) | Modo: NORMAL
    [Daily Meta] Progreso: $42.10 / $60.00 (70.2%) | Modo: NORMAL
    [Daily Meta] ✓ META ALCANZADA: $61.25 | Modo DEFENSIVO activado

════════════════════════════════════════════════════════════════════════════════════════
PASO 3: INTEGRACIÓN CON EQUITY BANDS
════════════════════════════════════════════════════════════════════════════════════════

Para máximo beneficio, usar AMBOS sistemas juntos:

    # Equity Bands: escala dinámicamente con capital
    APP_EQUITY_BANDS_ENABLED=true
    APP_EQUITY_BANDS=0:50,300:100,600:200,1000:300,1500:400

    # Daily Profit: protege ganancias del día
    APP_DAILY_PROFIT_TRACKING_ENABLED=true
    APP_DAILY_PROFIT_TARGET=60.0

SINERGIA:
    • Equity Bands: "¿Con qué capital juego?" (dinámico por tamaño cuenta)
    • Daily Profit: "¿Cuándo paro hoy?" (dinámico por ganancias del día)
    • Juntos: Escalado inteligente + disciplina = rentabilidad sostenible

════════════════════════════════════════════════════════════════════════════════════════
PASO 4: AJUSTAR SEGÚN TU CAPITAL
════════════════════════════════════════════════════════════════════════════════════════

CAPITAL $100-300:
    APP_DAILY_PROFIT_TARGET=20.0     # 20% del capital diario

CAPITAL $300-1000:
    APP_DAILY_PROFIT_TARGET=50.0     # 5-10% del capital diario

CAPITAL $1000+:
    APP_DAILY_PROFIT_TARGET=100.0    # 5-10% del capital diario

REGLA DE ORO:
    Meta_diaria = Capital × 0.05  a  Capital × 0.10

════════════════════════════════════════════════════════════════════════════════════════
PASO 5: MONITOREAR
════════════════════════════════════════════════════════════════════════════════════════

Archivo de estado: runtime/daily_profit_state.json

Contenido:
    {
        "date": "2026-05-07",
        "daily_pnl": 45.32,
        "daily_target": 60.0,
        "meta_reached": false,
        "defensive_mode": false,
        "trades_today": 12,
        "mode_since": "2026-05-07T14:30:00+00:00"
    }

Verificar:
    • daily_pnl: Ganancias hasta ahora
    • trades_today: Operaciones ejecutadas
    • defensive_mode: Si está activo
    • date: Confirmar que es hoy (si no, se reiniciará al arrancar)

════════════════════════════════════════════════════════════════════════════════════════
COMPORTAMIENTO ESPERADO
════════════════════════════════════════════════════════════════════════════════════════

ESCENARIO 1: En camino a meta
    Trade 1: +$12 (20% de meta)   → Modo AGGRESSIVE
    Trade 2: -$8  (15% de meta)   → Modo NORMAL
    Trade 3: +$25 (45% de meta)   → Modo NORMAL
    Progreso: [████░░░░░░░░░░░░░░░░]

ESCENARIO 2: Meta alcanzada
    Trade 8: +$5  (60% cumplido)  → Modo NORMAL
    Trade 9: +$3  (63% cumplido)  → ✓ META ALCANZADA
    Trade 10: -$10 (53% cumplido) → Modo DEFENSIVO
    Progreso: [████████████████████]

Comportamiento Defensivo:
    • Reduce stake por operación (ej: 50 → 30)
    • Aumenta % en favor si toma riesgo
    • Evita martingala agresiva
    • Objetivo: proteger ganancias del día

ESCENARIO 3: Rachas negativas
    Trade 1: -$15  → Progreso: [░░░░░░░░░░░░░░░░░░░░░] (0%)
    Trade 2: -$20  → Progreso: [░░░░░░░░░░░░░░░░░░░░░] (-58% de meta)
    Estado: daily_pnl = -35, día "en rojo"
    
    Comportamiento:
    • Continúa tratando de alcanzar meta (+60)
    • Pero con riesgo controlado
    • Nunca risquea más del 10% del capital
    • Reinicia mañana con contador limpio

════════════════════════════════════════════════════════════════════════════════════════
TROUBLESHOOTING
════════════════════════════════════════════════════════════════════════════════════════

❌ "No veo logs de [DailyProfit]"
   → APP_DAILY_PROFIT_TRACKING_ENABLED=false
   → Solución: Poner en true y reiniciar

❌ "Meta se alcanza pero no cambia riesgo"
   → APP_DAILY_PROFIT_DEFENSIVE_MODE=false
   → Solución: Poner en true para activar modo defensivo

❌ "Estado no persiste entre reinicios"
   → Verificar que runtime/ folder existe y tiene permisos
   → Verificar APP_DAILY_PROFIT_STATE_PATH es válido
   → Logs mostrarán "Error guardando estado"

❌ "Contador no se reinicia a medianoche"
   → Sistema usa date.today() UTC
   → Si tu servidor está en zona horaria diferente, usará eso
   → Solución: Configurar TZ environment variable

════════════════════════════════════════════════════════════════════════════════════════
PRÓXIMOS PASOS
════════════════════════════════════════════════════════════════════════════════════════

1. Copiar los 4 parámetros a .env
2. Reiniciar el bot
3. Verificar logs: "[DailyProfit] ACTIVO"
4. Ejecutar primer trade y confirmar que se loguea el progreso
5. Si alcanza meta, verifies "DEFENSIVE MODE ACTIVADO"
6. Revisar runtime/daily_profit_state.json para verificar persistencia

════════════════════════════════════════════════════════════════════════════════════════
