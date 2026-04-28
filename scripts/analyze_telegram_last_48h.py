from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient

from src.config.settings import AppSettings
from src.signals.parser import SignalParser


@dataclass
class MessageSample:
    chat: str
    date: datetime
    text: str


def _short(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


async def main() -> None:
    settings = AppSettings.load()
    if settings.telegram_api_id is None or not settings.telegram_api_hash:
        raise ValueError("Faltan credenciales de Telegram en .env")
    if not settings.telegram_source_chats:
        raise ValueError("Faltan chats en TELEGRAM_SOURCE_CHATS")

    parser = SignalParser(default_amount=settings.default_amount)
    from_date = datetime.now(timezone.utc) - timedelta(hours=48)

    total_by_chat: dict[str, int] = defaultdict(int)
    parsed_by_chat: dict[str, int] = defaultdict(int)
    side_counter: Counter[str] = Counter()
    asset_counter: Counter[str] = Counter()
    expiry_counter: Counter[int] = Counter()
    parsed_samples: list[MessageSample] = []
    unparsed_samples: list[MessageSample] = []

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    async with client:
        for chat_ref in settings.telegram_source_chats:
            entity = await client.get_entity(chat_ref)
            chat_name = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat_ref)

            async for msg in client.iter_messages(entity, offset_date=datetime.now(timezone.utc), reverse=False):
                if not msg.date or msg.date < from_date:
                    break

                text = (msg.raw_text or "").strip()
                if not text:
                    continue

                total_by_chat[chat_name] += 1
                parsed = parser.parse(text)

                if parsed is None:
                    if len(unparsed_samples) < 12:
                        unparsed_samples.append(MessageSample(chat=chat_name, date=msg.date, text=text))
                    continue

                parsed_by_chat[chat_name] += 1
                side_counter[parsed.side] += 1
                asset_counter[parsed.asset] += 1
                expiry_counter[parsed.expiry_minutes] += 1

                if len(parsed_samples) < 12:
                    parsed_samples.append(MessageSample(chat=chat_name, date=msg.date, text=text))

    total_msgs = sum(total_by_chat.values())
    total_parsed = sum(parsed_by_chat.values())
    ratio = (100.0 * total_parsed / total_msgs) if total_msgs else 0.0

    print("=== ANALISIS TELEGRAM (ULTIMAS 48H) ===")
    print(f"Mensajes totales: {total_msgs}")
    print(f"Mensajes parseados como senal: {total_parsed} ({ratio:.1f}%)")
    print("\n-- Cobertura por chat --")
    for chat_name in sorted(total_by_chat.keys()):
        t = total_by_chat[chat_name]
        p = parsed_by_chat.get(chat_name, 0)
        r = (100.0 * p / t) if t else 0.0
        print(f"{chat_name}: {p}/{t} ({r:.1f}%)")

    print("\n-- Distribucion parseada --")
    print("Lado:", dict(side_counter))
    print("Activos top:", asset_counter.most_common(8))
    print("Expiraciones top:", expiry_counter.most_common(8))

    print("\n-- Ejemplos parseados --")
    for idx, sample in enumerate(parsed_samples, start=1):
        print(f"{idx}. [{sample.chat}] {sample.date.isoformat()} | {_short(sample.text)}")

    print("\n-- Ejemplos NO parseados --")
    for idx, sample in enumerate(unparsed_samples, start=1):
        print(f"{idx}. [{sample.chat}] {sample.date.isoformat()} | {_short(sample.text)}")


if __name__ == "__main__":
    asyncio.run(main())
