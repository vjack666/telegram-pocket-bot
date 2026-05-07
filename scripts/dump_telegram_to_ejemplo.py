"""
Descarga todos los mensajes del canal VIP desde el 1 de enero de 2026 hasta hoy
y sobreescribe ejemplo.md con el formato:

    [DD/MM/YYYY HH:MM:SS] <nombre_canal>: <texto>

Uso:
    .venv\Scripts\python.exe scripts\dump_telegram_to_ejemplo.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import AppSettings
from telethon import TelegramClient

# ── Configuración ─────────────────────────────────────────────────────────────
DESDE = datetime(2026, 1, 1, tzinfo=timezone.utc)
HASTA = datetime.now(timezone.utc)
OUTPUT = ROOT / "ejemplo.md"
# ─────────────────────────────────────────────────────────────────────────────


async def main() -> None:
    settings = AppSettings.load()

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("ERROR: Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH en el .env")
        sys.exit(1)

    if not settings.telegram_source_chats:
        print("ERROR: Falta TELEGRAM_SOURCE_CHATS en el .env")
        sys.exit(1)

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    all_messages: list[tuple[datetime, str, str]] = []  # (date, canal, texto)

    async with client:
        for chat_ref in settings.telegram_source_chats:
            entity = await client.get_entity(chat_ref)
            # Nombre del canal igual que en ejemplo.md
            raw_key = chat_ref if isinstance(chat_ref, str) else str(chat_ref)
            canal = (
                settings.telegram_channel_names.get(raw_key)
                or getattr(entity, "title", None)
                or raw_key
            )

            print(f"Descargando desde '{canal}' ({chat_ref}) ...")
            count = 0

            # iter_messages descarga del más reciente al más antiguo;
            # offset_date=HASTA recorta los mensajes posteriores a HASTA.
            async for msg in client.iter_messages(
                entity,
                offset_date=HASTA,
                reverse=False,
            ):
                if msg.date is None:
                    continue
                # Parar cuando llegamos antes de DESDE
                if msg.date < DESDE:
                    break

                text = (msg.raw_text or "").strip()
                if not text:
                    continue

                all_messages.append((msg.date, canal, text))
                count += 1
                if count % 500 == 0:
                    print(f"  ... {count} mensajes descargados hasta ahora")

            print(f"  Total '{canal}': {count} mensajes")

    if not all_messages:
        print("No se encontraron mensajes en el rango indicado.")
        sys.exit(0)

    # Ordenar cronológicamente (ascendente)
    all_messages.sort(key=lambda x: x[0])

    # Convertir a zona horaria local implícita del canal (los timestamps en
    # ejemplo.md están en UTC-2 / hora del canal).  Telegram devuelve UTC,
    # así que escribimos UTC directamente para mantener consistencia con el
    # parser existente — o en hora local si el archivo original usa hora local.
    # ejemplo.md usa hora local (UTC-5 aprox según los horarios visibles),
    # pero el parser acepta cualquier zona siempre que sea consistente.
    # → Guardamos en UTC para máxima fidelidad; si el parser ya funciona bien
    #   con el archivo actual no hay problema al reemplazarlo.

    lines: list[str] = []
    for date_utc, canal, text in all_messages:
        ts = date_utc.strftime("%d/%m/%Y %H:%M:%S")
        lines.append(f"[{ts}] {canal}: {text}\n")

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nArchivo guardado: {OUTPUT}")
    print(f"Total mensajes escritos: {len(all_messages)}")
    print(f"Rango: {all_messages[0][0].strftime('%d/%m/%Y')} → {all_messages[-1][0].strftime('%d/%m/%Y')}")


if __name__ == "__main__":
    asyncio.run(main())
