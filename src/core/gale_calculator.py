"""
GaleCalculator — Lógica de stake con objetivo de entero par.

REGLA COMPLETA DE CÁLCULO DE STAKE:
─────────────────────────────────────────────────────────────────────────
Objetivo: la cuenta siempre debe crecer hacia el siguiente número entero
par (sin decimales) más cercano por encima del saldo actual, avanzando
de 2 en 2 dentro de la secuencia: ..., 146, 148, 150, 152, 154, ...

Paso 1 — Calcular objetivo:
  target = int(saldo) + incremento_configurado, redondeado al par más
  cercano hacia arriba si es impar.
  - Saldo <= incremento_umbral ($100): incremento = APP_CALC_INCREMENT_BELOW_100 (def. 1)
  - Saldo >  incremento_umbral ($100): incremento = APP_CALC_INCREMENT          (def. 2)

Paso 2 — Calcular stake:
  stake = (target - saldo) / payout

Paso 3 — Saltar si el stake es < mínimo_inversión (defecto $1.00):
  Mientras stake < minimo_inversion_objetivo:
      target += 2   (siguiente entero par)
      stake = (target - saldo) / payout

Estado estacionario (saldo exactamente en número par):
  saldo=$150 → target=$152 → stake=2/0.92=$2.17 → ganancia=$2.00

Con decimales (saldo fuera de par):
  saldo=$25.99  → target inicial $26, stake $0.01 → salta a $28, stake≈$2.18
  saldo=$148.17 → target $150, stake $1.99 → ganancia $1.83 (vuelve al track)
─────────────────────────────────────────────────────────────────────────
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class GaleCalculator:
    saldo_actual: float
    payout: float  # Ej: 0.92 para 92%
    incremento: int = 2  # Incremento para saldo por encima del umbral (APP_CALC_INCREMENT)
    incremento_bajo_umbral: int = 1  # Incremento por debajo/igual al umbral (APP_CALC_INCREMENT_BELOW_100)
    incremento_umbral: float = 100.0  # Umbral que separa los dos incrementos (APP_CALC_INCREMENT_THRESHOLD)
    objetivo_entero_par: bool = True
    objetivo_manual: Optional[float] = None  # Si se usa objetivo manual
    usar_multiplicador: bool = False
    multiplicador: float = 2.0
    perdidas: int = 0
    inversion_base: float = 0.0
    inversion_actual: float = 0.0
    saldo_objetivo: float = 0.0
    regla10_limite: float = 50.0
    regla10_tolerancia_pct: float = 0.005
    minimo_inversion_objetivo: float = 1.0
    mensaje: str = ""

    def regla10_activa(self) -> bool:
        return self.saldo_actual > self.regla10_limite

    def _incremento_actual(self) -> int:
        """Retorna el incremento configurado según el saldo actual.

        - Saldo <= incremento_umbral: usa incremento_bajo_umbral (APP_CALC_INCREMENT_BELOW_100, defecto 1)
        - Saldo >  incremento_umbral: usa incremento             (APP_CALC_INCREMENT,          defecto 2)
        """
        if self.saldo_actual <= self.incremento_umbral:
            return self.incremento_bajo_umbral
        return self.incremento

    def calcular_objetivo(self):
        """Determina saldo_objetivo: el entero par por encima del saldo actual.

        REGLA: el target siempre avanza de 2 en 2 dentro de la secuencia de
        enteros pares (...148, 150, 152...). El incremento configurable determina
        el punto de partida; el bucle en recalcular_inversion() salta hacia
        adelante si el stake resultante es menor al mínimo operativo.
        """
        if self.objetivo_manual and self.objetivo_manual > self.saldo_actual:
            self.saldo_objetivo = self.objetivo_manual
        else:
            objetivo = int(self.saldo_actual) + self._incremento_actual()
            if self.objetivo_entero_par:
                if objetivo % 2 != 0:
                    objetivo += 1
            self.saldo_objetivo = float(objetivo)

    def recalcular_inversion(self):
        """Calcula inversion_base e inversion_actual para alcanzar saldo_objetivo.

        1. Fija el target via calcular_objetivo().
        2. stake = (target - saldo) / payout
        3. Si stake < minimo_inversion_objetivo, avanza target de 2 en 2
           hasta que el stake sea operable (máx. 50 saltos).
        """
        self.calcular_objetivo()
        if self.payout <= 0:
            self.inversion_base = 0
            self.inversion_actual = 0
            return

        utilidad_necesaria = self.saldo_objetivo - self.saldo_actual
        self.inversion_base = utilidad_necesaria / self.payout if utilidad_necesaria > 0 else 0

        if not self.objetivo_manual:
            paso_objetivo = 2 if self.objetivo_entero_par else 1
            max_saltos = 50
            saltos = 0
            minimo = max(0.0, float(self.minimo_inversion_objetivo))
            while self.inversion_base > 0 and self.inversion_base < minimo and saltos < max_saltos:
                self.saldo_objetivo += float(paso_objetivo)
                utilidad_necesaria = self.saldo_objetivo - self.saldo_actual
                self.inversion_base = utilidad_necesaria / self.payout if utilidad_necesaria > 0 else 0
                saltos += 1

        self.inversion_actual = self.inversion_base

    def _recalcular_inversion_sin_objetivo(self):
        """Recalcula inversion_actual manteniendo saldo_objetivo constante (para gales)."""
        utilidad_necesaria = self.saldo_objetivo - self.saldo_actual
        if self.payout <= 0:
            self.inversion_base = 0
            self.inversion_actual = 0
            return
        self.inversion_base = utilidad_necesaria / self.payout if utilidad_necesaria > 0 else 0
        self.inversion_actual = self.inversion_base

    def on_gano(self):
        ganancia = self.inversion_actual * self.payout
        self.saldo_actual += ganancia
        self.saldo_actual = self.saldo_objetivo  # Forzar cierre limpio
        self.perdidas = 0
        self.objetivo_manual = None
        self.recalcular_inversion()
        self.mensaje = f"✅ Ganaste +${ganancia:.2f} | objetivo: ${self.saldo_actual:.2f}"

    def on_perdio(self):
        self.saldo_actual -= self.inversion_actual
        if self.saldo_actual < 0:
            self.saldo_actual = 0
        self.perdidas += 1
        limite = self.saldo_actual * (0.10 + max(0.0, self.regla10_tolerancia_pct))
        if not self.regla10_activa() and self.perdidas >= 3:
            self.perdidas = 0
            self.recalcular_inversion()
            self.mensaje = '🔄 Reset gale por 3 pérdidas (saldo <= $50)'
            return
        if self.usar_multiplicador:
            siguiente = self.inversion_actual * self.multiplicador
        else:
            utilidad_necesaria = self.saldo_objetivo - self.saldo_actual
            siguiente = utilidad_necesaria / self.payout if self.payout > 0 and utilidad_necesaria > 0 else 0
        if self.regla10_activa() and siguiente >= limite:
            self.perdidas = 0
            self.recalcular_inversion()
            self.mensaje = '⚠️ Reset por riesgo (>10% de la cuenta)'
            return
        self.inversion_actual = siguiente
        self.mensaje = f'❌ Perdiste - Gale {self.perdidas}'

    def get_estado(self):
        return {
            'saldo_actual': self.saldo_actual,
            'saldo_objetivo': self.saldo_objetivo,
            'inversion_actual': self.inversion_actual,
            'perdidas': self.perdidas,
            'mensaje': self.mensaje,
            'regla10_activa': self.regla10_activa(),
            'regla10_tolerancia_pct': self.regla10_tolerancia_pct,
            'minimo_inversion_objetivo': self.minimo_inversion_objetivo,
            'incremento_actual': self._incremento_actual(),
            'incremento_regla': 'umbral_configurable',
            'incremento_bajo_umbral': self.incremento_bajo_umbral,
            'incremento_alto': self.incremento,
            'incremento_umbral': self.incremento_umbral,
            'objetivo_entero_par': self.objetivo_entero_par,
        }
