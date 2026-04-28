import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import AppSettings
from src.pocket_option.client import PocketOptionDemoClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prueba directa del cambio de activo en Pocket Option sin Telegram.",
    )
    parser.add_argument(
        "target_asset",
        nargs="?",
        default="USDCHF OTC",
        help="Activo destino a seleccionar. Ejemplo: 'USDCHF OTC'",
    )
    parser.add_argument("--open-selector", default="", help="Override de POCKET_ASSET_OPEN_SELECTOR")
    parser.add_argument("--search-selector", default="", help="Override de POCKET_ASSET_SEARCH_SELECTOR")
    parser.add_argument("--result-selector", default="", help="Override de POCKET_ASSET_RESULT_SELECTOR")
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Deja el navegador abierto al terminar la prueba.",
    )
    return parser


async def wait_for_page_open(page, timeout_seconds: int = 10) -> bool:
    deadline = time.monotonic() + timeout_seconds
    last_remaining = None

    while time.monotonic() < deadline:
        remaining = max(0, int(deadline - time.monotonic()) + 1)
        if remaining != last_remaining:
            logging.info("Esperando que abra la pagina... %ss", remaining)
            last_remaining = remaining

        try:
            url = (page.url or "").strip()
            if url and url != "about:blank":
                logging.info("Pagina detectada abierta: %s", url)
                return True
        except Exception:
            pass

        await asyncio.sleep(0.5)

    return False


async def main() -> int:
    args = build_parser().parse_args()
    settings = AppSettings.load()
    exit_code = 1

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    open_selector = args.open_selector.strip() or settings.pocket_asset_open_selector
    search_selector = args.search_selector.strip() or settings.pocket_asset_search_selector
    result_selector = args.result_selector.strip() or settings.pocket_asset_result_selector

    client = PocketOptionDemoClient(
        account_mode=settings.pocket_account_mode,
        default_asset=settings.default_asset,
        demo_url=settings.pocket_demo_url,
        profile_dir=settings.pocket_profile_dir,
        headless=settings.pocket_headless,
        execute_orders=False,
        max_order_amount=settings.pocket_max_order_amount,
        balance_selector=settings.pocket_balance_selector,
        asset_open_selector=open_selector,
        asset_search_selector=search_selector,
        asset_result_selector=result_selector,
        buy_selector=settings.pocket_buy_selector,
        sell_selector=settings.pocket_sell_selector,
        amount_selector=settings.pocket_amount_selector,
    )

    try:
        await client.connect()
        page = client._page
        if page is None:
            logging.error("No se pudo obtener la pagina de Pocket Option.")
            exit_code = 3
            return exit_code

        if not await wait_for_page_open(page, timeout_seconds=10):
            logging.error("La pagina no se detecto abierta dentro de los 10 segundos de espera.")
            exit_code = 3
            return exit_code

        title = await page.title()
        url = page.url
        logging.info("URL actual: %s", url)
        logging.info("Titulo actual: %s", title)

        lowered = f"{url} {title}".lower()
        if any(token in lowered for token in ("login", "sign in", "signin", "auth")):
            logging.error(
                "La sesion parece cerrada. Abre Pocket Option con el perfil persistente, inicia sesion y vuelve a correr la prueba."
            )
            exit_code = 4
            return exit_code

        logging.info("La plataforma abrio y la sesion parece disponible.")

        if not open_selector:
            logging.info("Sin open-selector explicito: se usaran fallbacks integrados del cliente.")
        if not search_selector:
            logging.info("Sin search-selector explicito: se usara el input dentro de .drop-down-modal--quotes-list.")
        if not result_selector:
            logging.info("Sin result-selector explicito: se usaran resultados visibles dentro del modal de activos.")

        current_asset = await client._get_current_asset()
        logging.info("Activo detectado antes de la prueba: %s", current_asset or "desconocido")
        logging.info("Intentando cambiar grafica a: %s", args.target_asset)

        await client.ensure_asset(args.target_asset)

        final_asset = await client._get_current_asset()
        logging.info("Activo detectado despues de la prueba: %s", final_asset or "desconocido")
        print("\nRESULTADO: cambio de grafica completado")
        print(f"ANTES={current_asset or 'desconocido'}")
        print(f"DESPUES={final_asset or 'desconocido'}")
        exit_code = 0
        return exit_code
    except Exception as exc:
        logging.exception("La prueba de cambio de grafica fallo: %s", exc)
        exit_code = 1
        return exit_code
    finally:
        if not args.keep_open:
            await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))