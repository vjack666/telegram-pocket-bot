import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient


def _parse_api_id(value: str) -> int:
    value = (value or "").strip()
    if not value:
        raise ValueError("Falta TELEGRAM_API_ID en .env")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError("TELEGRAM_API_ID debe ser entero") from exc


def _required(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ValueError(f"Falta {name} en .env")
    return value


async def _main() -> None:
    load_dotenv()

    api_id = _parse_api_id(os.getenv("TELEGRAM_API_ID", ""))
    api_hash = _required("TELEGRAM_API_HASH")
    session_name = (os.getenv("TELEGRAM_SESSION_NAME") or "signal_reader").strip() or "signal_reader"

    print(f"Sesion objetivo: {session_name}")
    print("Si la sesión estaba inválida, este proceso pedirá código y 2FA si aplica.")

    client = TelegramClient(session_name, api_id, api_hash)
    async with client:
        await client.start()
        me = await client.get_me()
        username = getattr(me, "username", None) or "sin_username"
        print(f"Autenticado correctamente como: id={me.id} username={username}")

    session_file = Path.cwd() / f"{session_name}.session"
    if session_file.exists():
        print(f"Archivo de sesión generado/actualizado: {session_file}")
    else:
        print("No se encontró el archivo .session en el cwd. Revisa TELEGRAM_SESSION_NAME y ruta de ejecución.")


if __name__ == "__main__":
    asyncio.run(_main())
