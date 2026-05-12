"""Base de datos de aprendizaje por sesión (JSONL).

Cada línea del archivo JSONL representa una sesión completa con:
- Escenario Masaniello (M1/M2/M3, secuencia W/L)
- Resultado del G2 si hubo intervención humana
- Nota de condición de mercado (si el usuario la proveyó)
- PnL de la sesión
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class SessionLearningDB:
    """Escritor append-only de registros de sesión en formato JSONL."""

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_session(
        self,
        *,
        asset: str,
        side: str,
        masaniello_sequence: str,          # ej. "WL", "LL", "W"
        masaniello_label: str,             # ej. "M3 tras 2L"
        step_reached: int,                 # 0=E, 1=G1, 2=G2
        g2_intervened: bool,               # True = el humano fue consultado
        g2_approved: Optional[bool],       # True=aprobó, False=canceló, None=no llegó a G2
        g2_amount: Optional[float],        # monto del G2 consultado
        session_pnl: float,                # ganancia/pérdida final del ciclo
        won: bool,                         # True si el ciclo terminó en WIN
        market_note: str = "",             # nota del usuario: "Volátil", "Lento", "Tendencial"
        expiry_minutes: int = 1,
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "asset": asset,
            "side": side,
            "masaniello_sequence": masaniello_sequence,
            "masaniello_label": masaniello_label,
            "step_reached": step_reached,
            "g2_intervened": g2_intervened,
            "g2_approved": g2_approved,
            "g2_amount": g2_amount,
            "session_pnl": round(session_pnl, 4),
            "won": won,
            "market_note": market_note.strip(),
            "expiry_minutes": expiry_minutes,
        }
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            logging.debug("[LearningDB] Sesión registrada: %s", record)
        except Exception as exc:
            logging.warning("[LearningDB] No se pudo guardar registro: %s", exc)

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        records: list[dict] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    def summary(self) -> dict:
        records = self.read_all()
        total = len(records)
        if total == 0:
            return {"total": 0}
        won = sum(1 for r in records if r.get("won"))
        g2_intervened = [r for r in records if r.get("g2_intervened")]
        g2_saved = sum(1 for r in g2_intervened if r.get("g2_approved") is False and r.get("won") is False)
        return {
            "total": total,
            "won": won,
            "lost": total - won,
            "win_rate_pct": round(100 * won / total, 1),
            "g2_interventions": len(g2_intervened),
            "g2_approved": sum(1 for r in g2_intervened if r.get("g2_approved") is True),
            "g2_cancelled": sum(1 for r in g2_intervened if r.get("g2_approved") is False),
            "avg_pnl": round(sum(r.get("session_pnl", 0) for r in records) / total, 4),
        }
