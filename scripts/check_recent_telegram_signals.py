#!/usr/bin/env python3
"""Lee mensajes recientes de Telegram y evalua si una senal aun es tomable.

Uso:
  "c:/Users/v_jac/Desktop/poket option/.venv/Scripts/python.exe" scripts/check_recent_telegram_signals.py
  "c:/Users/v_jac/Desktop/poket option/.venv/Scripts/python.exe" scripts/check_recent_telegram_signals.py --seconds 120 --limit 30
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from telethon import TelegramClient

from src.config.settings import AppSettings
from src.signals.parser import SignalParser


@dataclass
class EvaluatedMessage:
    channel: str
    message_id: int
    message_time_utc: datetime
    delay_seconds: float
    status: str
    details: str


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return digits


async def _resolve_chat(client: TelegramClient, raw: str):
    chat = (raw or "").strip()
    if not chat:
        return None

    try:
        return await client.get_entity(chat)
    except Exception:
        pass

    normalized_phone = _normalize_phone(chat)
    if not normalized_phone:
        return None

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        phone = _normalize_phone(getattr(entity, "phone", ""))
        if phone and phone == normalized_phone:
            return entity

    return None


def _channel_name(entity, raw_chat: str, configured_names: dict[str, str]) -> str:
    if raw_chat in configured_names:
        return configured_names[raw_chat]
    return (
        getattr(entity, "title", None)
        or getattr(entity, "username", None)
        or raw_chat
    )


def _evaluate_signal_status(now_utc: datetime, execute_at_utc: datetime | None, late_tolerance: int) -> tuple[str, str]:
    if execute_at_utc is None:
        return ("ACTIONABLE_NOW", "sin hora explicita; se puede tomar inmediato")

    delta = (execute_at_utc - now_utc).total_seconds()
    if delta > 0:
        return ("ACTIONABLE_WAIT", f"entra en {delta:.1f}s")

    if abs(delta) <= late_tolerance:
        return ("ACTIONABLE_LATE", f"llega tarde por {abs(delta):.1f}s (dentro de tolerancia)")

    return ("EXPIRED", f"vencida por {abs(delta):.1f}s")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evalua si las ultimas senales de Telegram aun se pueden tomar")
    parser.add_argument("--seconds", type=int, default=90, help="Ventana de lectura reciente en segundos")
    parser.add_argument("--limit", type=int, default=20, help="Mensajes maximos por chat")
    args = parser.parse_args()

    settings = AppSettings.load()
    if not settings.enable_telegram:
        raise RuntimeError("APP_ENABLE_TELEGRAM=false. Activa Telegram en .env para usar este script.")
    if settings.telegram_api_id is None:
        raise RuntimeError("Falta TELEGRAM_API_ID en .env")

    signal_parser = SignalParser(
        default_amount=settings.default_amount,
        signal_tz_offset_hours=settings.expected_utc_offset_hours,
        signal_timezone=settings.signal_timezone,
    )

    cutoff_utc = datetime.now(timezone.utc) - timedelta(seconds=max(1, args.seconds))
    now_utc = datetime.now(timezone.utc)

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        auto_reconnect=True,
        connection_retries=3,
    )

    evaluated: list[EvaluatedMessage] = []

    try:
        await client.start()
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            raise RuntimeError(
                "La sesion de Telegram esta en uso por otro proceso (main.py). "
                "Cierra temporalmente el bot o usa otra session para inspeccion."
            ) from exc
        raise
    try:
        for raw_chat in settings.telegram_source_chats:
            entity = await _resolve_chat(client, raw_chat)
            if entity is None:
                evaluated.append(
                    EvaluatedMessage(
                        channel=raw_chat,
                        message_id=0,
                        message_time_utc=now_utc,
                        delay_seconds=0.0,
                        status="CHAT_UNRESOLVED",
                        details="no se pudo resolver chat",
                    )
                )
                continue

            channel = _channel_name(entity, raw_chat, settings.telegram_channel_names)

            async for msg in client.iter_messages(entity, limit=max(1, args.limit)):
                if not msg.date:
                    continue
                msg_utc = msg.date.astimezone(timezone.utc)
                if msg_utc < cutoff_utc:
                    break

                text = (msg.raw_text or "").strip()
                if not text:
                    continue

                delay = (now_utc - msg_utc).total_seconds()
                parsed = signal_parser.parse(text, received_at_utc=msg_utc)
                if parsed is None:
                    evaluated.append(
                        EvaluatedMessage(
                            channel=channel,
                            message_id=msg.id,
                            message_time_utc=msg_utc,
                            delay_seconds=delay,
                            status="NO_SIGNAL",
                            details="texto sin formato de senal",
                        )
                    )
                    continue

                status, reason = _evaluate_signal_status(
                    now_utc=now_utc,
                    execute_at_utc=parsed.execute_at_utc,
                    late_tolerance=settings.signal_late_tolerance_seconds,
                )
                details = (
                    f"{parsed.asset} {parsed.side} exp={parsed.expiry_minutes}m | "
                    f"entry={parsed.execute_at_utc.isoformat() if parsed.execute_at_utc else 'NOW'} | {reason}"
                )
                evaluated.append(
                    EvaluatedMessage(
                        channel=channel,
                        message_id=msg.id,
                        message_time_utc=msg_utc,
                        delay_seconds=delay,
                        status=status,
                        details=details,
                    )
                )
    finally:
        await client.disconnect()

    evaluated.sort(key=lambda item: item.message_time_utc, reverse=True)

    print("\nULTIMOS MENSAJES EVALUADOS")
    print("=" * 90)
    if not evaluated:
        print("No hubo mensajes en la ventana solicitada.")
        return

    for item in evaluated:
        stamp = item.message_time_utc.isoformat()
        print(
            f"[{item.status}] canal='{item.channel}' msg_id={item.message_id} "
            f"msg_utc={stamp} delay={item.delay_seconds:.1f}s"
        )
        print(f"  {item.details}")


if __name__ == "__main__":
    asyncio.run(main())
