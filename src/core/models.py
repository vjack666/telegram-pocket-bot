from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class TradingSignal:
    asset: str
    side: str
    expiry_minutes: int
    amount: float
    source_text: str
    received_at: datetime
    execute_at_utc: datetime | None = None
    martingale_execute_at_utc: tuple[datetime, ...] = ()
    source_name: str = ""  # canal de Telegram que originó la señal
    session_start_utc: datetime | None = None  # hora de inicio de sesión (UTC-3 → UTC)
    session_end_utc: datetime | None = None    # hora de fin de sesión (UTC-3 → UTC)

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(tz=timezone.utc)
