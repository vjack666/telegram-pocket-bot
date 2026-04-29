from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TelegramInboundMessage:
    chat_id: int
    message_id: int
    text: str
    message_date_utc: datetime
    received_at_utc: datetime
    source_name: str = ""  # nombre del canal de origen (ej: "VIP TRADER A")
