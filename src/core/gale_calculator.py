"""
GaleCalculator: Lógica de gestión de riesgo tipo gale/martingala y objetivo incremental/manual.
Portado desde la calculadora Flutter/Dart.
"""

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class GaleCalculator:
    saldo_actual: float
    payout: float  # Ej: 0.92 para 92%
    incremento: int = 2  # Incremento para saldo por encima del umbral
    incremento_bajo_umbral: int = 1  # Incremento para saldo por debajo/igual al umbral
    incremento_umbral: float = 100.0
    objetivo_entero_par: bool = True
    objetivo_manual: Optional[float] = None  # Si se usa objetivo manual
    usar_multiplicador: bool = False
    multiplicador: float = 2.0
    perdidas: int = 0
    inversion_base: float = 0.0
    inversion_actual: float = 0.0
    saldo_objetivo: float = 0.0
    regla10_limite: float = 50.0  # Límite para activar regla 10%
    regla10_tolerancia_pct: float = 0.005  # Margen extra (0.5%) para no resetear por cercania al 10%
    mensaje: str = ""

    def regla10_activa(self) -> bool:
        return self.saldo_actual > self.regla10_limite

    def _incremento_actual(self) -> int:
        # Regla dinámica: 1% por cada bloque de $100 del saldo.
        # Ejemplos: 300 -> 3, 400 -> 4, con mínimo 1.
        bloques_100 = int(max(0.0, float(self.saldo_actual)) // 100.0)
        return max(1, bloques_100)

    def calcular_objetivo(self):
        if self.objetivo_manual and self.objetivo_manual > self.saldo_actual:
            self.saldo_objetivo = self.objetivo_manual
        else:
            objetivo = int(self.saldo_actual) + self._incremento_actual()
            if self.objetivo_entero_par:
                if objetivo % 2 != 0:
                    objetivo += 1
            self.saldo_objetivo = float(objetivo)

    def recalcular_inversion(self):
        self.calcular_objetivo()
        utilidad_necesaria = self.saldo_objetivo - self.saldo_actual
        if self.payout <= 0:
            self.inversion_base = 0
            self.inversion_actual = 0
            return
        self.inversion_base = utilidad_necesaria / self.payout if utilidad_necesaria > 0 else 0
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
        # Gale intermedio: mantener objetivo fijo, solo actualizar inversion_actual
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
            'incremento_actual': self._incremento_actual(),
            'incremento_regla': '1pct_por_cada_100_usd_min_1',
            'incremento_bajo_umbral': self.incremento_bajo_umbral,
            'incremento_alto': self.incremento,
            'incremento_umbral': self.incremento_umbral,
            'objetivo_entero_par': self.objetivo_entero_par,
        }
