"""
inspect_candle_ws.py
====================
Inspecciona los frames WebSocket que Pocket Option envía al navegador.
Muestra los mensajes en crudo para identificar el formato de precios/velas.

Uso:
    python scripts/inspect_candle_ws.py
    python scripts/inspect_candle_ws.py --asset EURUSD --seconds 30
    python scripts/inspect_candle_ws.py --dump-binary   # muestra hex además de texto

Presiona Ctrl+C para detener antes del timeout.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

DEMO_URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE_DIR = ".pocket_profile"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _try_decode(payload: str | bytes) -> str:
    """Intenta decodificar payload como JSON o UTF-8."""
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:
            return payload.hex()
    else:
        text = payload

    try:
        parsed = json.loads(text)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except Exception:
        return text


def _is_price_message(payload: str | bytes) -> bool:
    """Heurística: ¿el mensaje parece contener datos de precio?"""
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:
            text = ""
    else:
        text = payload
    lower = text.lower()
    return any(kw in lower for kw in ("quote", "tick", "candle", "ohlc", "price", "asset", "open", "close", "high", "low"))


async def run(asset: str, seconds: int, dump_binary: bool, price_only: bool) -> None:
    profile_path = str(Path(PROFILE_DIR).resolve())
    total_frames = 0
    price_frames = 0

    async with async_playwright() as pw:
        log.info("Abriendo perfil en %s", profile_path)
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
        )
        page = context.pages[0] if context.pages else await context.new_page()

        ws_list: list[str] = []

        def on_ws(ws):
            url = ws.url
            ws_list.append(url)
            log.info("[WS CONNECT] %s", url)

            def on_frame_received(payload):
                nonlocal total_frames, price_frames
                total_frames += 1
                is_price = _is_price_message(payload)
                if is_price:
                    price_frames += 1

                if price_only and not is_price:
                    return

                now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                kind = "PRICE" if is_price else "other"
                decoded = _try_decode(payload)

                if isinstance(payload, bytes) and dump_binary:
                    hex_str = payload.hex()
                    print(f"\n[{now}] WS←server [{kind}] len={len(payload)} hex={hex_str[:120]}")
                    print(decoded[:800])
                else:
                    print(f"\n[{now}] WS←server [{kind}] len={len(payload) if isinstance(payload, bytes) else len(str(payload))}")
                    print(decoded[:1200])

            def on_frame_sent(payload):
                now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                decoded = _try_decode(payload)
                print(f"\n[{now}] WS→server len={len(payload) if isinstance(payload, bytes) else len(str(payload))}")
                print(decoded[:800])

            def on_close():
                log.info("[WS CLOSE] %s", url)

            ws.on("framereceived", on_frame_received)
            ws.on("framesent", on_frame_sent)
            ws.on("close", on_close)

        page.on("websocket", on_ws)

        log.info("Navegando a %s", DEMO_URL)
        await page.goto(DEMO_URL, wait_until="domcontentloaded", timeout=45_000)

        # Si se especificó un activo, intentar seleccionarlo
        if asset.upper() != "DEFAULT":
            log.info("Activo solicitado: %s (cambia manualmente si no lo hace solo)", asset.upper())

        log.info("Escuchando WebSocket por %s segundos... (Ctrl+C para parar)", seconds)
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass

        log.info("Resumen: %d frames totales, %d con keywords de precio", total_frames, price_frames)
        log.info("WebSockets vistos: %s", ws_list)

        await context.close()


def main():
    parser = argparse.ArgumentParser(description="Inspecciona frames WebSocket de Pocket Option")
    parser.add_argument("--asset", default="DEFAULT", help="Activo a observar (visual)")
    parser.add_argument("--seconds", type=int, default=30, help="Segundos a escuchar (default: 30)")
    parser.add_argument("--dump-binary", action="store_true", help="Muestra hex de frames binarios")
    parser.add_argument("--price-only", action="store_true", help="Solo muestra mensajes de precio (filtra ruido)")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.asset, args.seconds, args.dump_binary, args.price_only))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")


if __name__ == "__main__":
    main()
