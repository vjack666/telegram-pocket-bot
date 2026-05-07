"""
dump_telegram_history.py
------------------------
Descarga el historial de los últimos N días de los canales Telegram configurados
en .env y lo agrega al archivo ejemplo.md (o cualquier archivo destino).

Uso:
    python scripts/dump_telegram_history.py
    python scripts/dump_telegram_history.py --days 60 --output ejemplo.md
    python scripts/dump_telegram_history.py --days 30 --output runtime/historial_nuevo.md
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Asegura que el root del proyecto esté en sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from telethon import TelegramClient

from src.config.settings import AppSettings


def _format_message(msg) -> str:
    """Formatea un mensaje Telegram igual que aparece en ejemplo.md."""
    if not msg.text:
        return ""
    date_str = msg.date.astimezone().strftime("%d/%m/%Y %H:%M:%S")
    sender = ""
    try:
        if msg.sender:
            sender = getattr(msg.sender, "title", None) or getattr(msg.sender, "first_name", None) or ""
    except Exception:
        pass
    header = f"[{date_str}] {sender}: " if sender else f"[{date_str}]: "
    return header + msg.text


async def main(days: int, output_path: Path, append: bool) -> None:
    settings = AppSettings.load()

    if settings.telegram_api_id is None or not settings.telegram_api_hash:
        print("ERROR: Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH en .env")
        sys.exit(1)

    if not settings.telegram_source_chats:
        print("ERROR: Falta TELEGRAM_SOURCE_CHATS en .env")
        sys.exit(1)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    print(f"Descargando mensajes desde: {since.strftime('%d/%m/%Y %H:%M UTC')} (últimos {days} días)")
    print(f"Canales: {settings.telegram_source_chats}")
    print(f"Destino: {output_path}  ({'append' if append else 'overwrite'})")
    print()

    # Usamos una sesión separada para no bloquear la sesión del bot principal
    dump_session = str(ROOT / (settings.telegram_session_name + "_dump"))
    client = TelegramClient(
        dump_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    all_messages: list[tuple[datetime, str]] = []

    async with client:
        for chat_ref in settings.telegram_source_chats:
            try:
                entity = await client.get_entity(chat_ref)
                chat_name = (
                    getattr(entity, "title", None)
                    or getattr(entity, "username", None)
                    or str(chat_ref)
                )
                print(f"Canal: {chat_name}")

                count = 0
                async for msg in client.iter_messages(
                    entity,
                    offset_date=datetime.now(timezone.utc),
                    reverse=False,
                    limit=None,
                ):
                    if not msg.date:
                        continue
                    if msg.date < since:
                        break
                    if not msg.text or not msg.text.strip():
                        continue

                    text = _format_message(msg)
                    if text:
                        all_messages.append((msg.date, text))
                        count += 1

                print(f"  → {count} mensajes descargados")

            except Exception as exc:
                print(f"  ERROR en {chat_ref}: {exc}")

    if not all_messages:
        print("\nNo se encontraron mensajes.")
        return

    # Ordenar cronológicamente (más antiguos primero)
    all_messages.sort(key=lambda x: x[0])

    # Escribir en el archivo
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(output_path, mode, encoding="utf-8") as f:
        if append:
            f.write("\n\n---\n<!-- dump adicional: " + datetime.now().strftime("%Y-%m-%d %H:%M") + " -->\n\n")
        for _, text in all_messages:
            f.write(text + "\n\n")

    print(f"\nTotal mensajes escritos: {len(all_messages)}")
    print(f"Archivo: {output_path.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Descarga historial Telegram a archivo .md")
    parser.add_argument("--days",   type=int,  default=60,           help="Días hacia atrás a descargar (default: 60)")
    parser.add_argument("--output", type=str,  default="ejemplo.md", help="Archivo destino (default: ejemplo.md)")
    parser.add_argument("--overwrite", action="store_true",          help="Sobreescribir el archivo en vez de agregar al final")
    args = parser.parse_args()

    output = Path(args.output) if Path(args.output).is_absolute() else ROOT / args.output
    asyncio.run(main(args.days, output, append=not args.overwrite))
