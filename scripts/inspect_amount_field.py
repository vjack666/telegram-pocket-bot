"""
inspect_amount_field.py
=======================
Inspecciona el DOM para encontrar el selector correcto del campo de monto.

Haz click en el campo de monto en el navegador, y este script capturará
el selector exacto que Playwright debe usar.

Uso:
    python scripts/inspect_amount_field.py
    python scripts/inspect_amount_field.py --keep-open
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

DEMO_URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE_DIR = ".pocket_profile"


async def run(keep_open: bool) -> None:
    import logging
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    profile_path = str(Path(PROFILE_DIR).resolve())
    log.info("Abriendo perfil en %s", profile_path)

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(DEMO_URL, wait_until="domcontentloaded", timeout=45_000)

        log.info("Navegador abierto. Esperando 2 segundos...")
        await asyncio.sleep(2)

        # Intentar localizar candidatos
        log.info("\n=== Búsqueda de candidatos de campo de monto ===\n")

        candidates = []

        # Candidato 1: input con class "*bet-amount*"
        try:
            inputs = await page.query_selector_all(".block--bet-amount input")
            if inputs:
                for idx, inp in enumerate(inputs):
                    val = await inp.get_attribute("value")
                    placeholder = await inp.get_attribute("placeholder")
                    type_attr = await inp.get_attribute("type")
                    name = await inp.get_attribute("name")
                    log.info(
                        "Candidato 1.%d: .block--bet-amount input[%d]\n"
                        "  value=%s placeholder=%s type=%s name=%s",
                        idx, idx, val, placeholder, type_attr, name
                    )
                    candidates.append((".block--bet-amount input", f"input {idx}"))
        except Exception as e:
            log.warning("Fallo buscando .block--bet-amount input: %s", e)

        # Candidato 2: input dentro de div con class "*amount*"
        try:
            divs = await page.query_selector_all("div[class*='amount'] input")
            if divs:
                for idx, inp in enumerate(divs):
                    val = await inp.get_attribute("value")
                    placeholder = await inp.get_attribute("placeholder")
                    log.info(
                        "Candidato 2.%d: div[class*='amount'] input\n"
                        "  value=%s placeholder=%s",
                        idx, val, placeholder
                    )
                    candidates.append(("div[class*='amount'] input", f"div amount {idx}"))
        except Exception as e:
            log.warning("Fallo buscando div[class*='amount'] input: %s", e)

        # Candidato 3: input type="number" en panel derecho
        try:
            numbers = await page.query_selector_all("input[type='number']")
            if numbers:
                for idx, inp in enumerate(numbers):
                    val = await inp.get_attribute("value")
                    placeholder = await inp.get_attribute("placeholder")
                    parent_class = await page.evaluate(
                        "el => el.parentElement?.className || ''",
                        numbers[idx]
                    )
                    log.info(
                        "Candidato 3.%d: input[type='number']\n"
                        "  value=%s placeholder=%s parent_class=%s",
                        idx, val, placeholder, parent_class
                    )
                    candidates.append(("input[type='number']", f"number {idx}"))
        except Exception as e:
            log.warning("Fallo buscando input[type='number']: %s", e)

        # Candidato 4: escáner genérico de inputs visibles
        try:
            all_inputs = await page.query_selector_all("input")
            log.info("\n=== Todos los inputs en la página (%d) ===\n", len(all_inputs))
            for idx, inp in enumerate(all_inputs[:15]):  # primeros 15
                try:
                    val = await inp.get_attribute("value")
                    type_attr = await inp.get_attribute("type")
                    name = await inp.get_attribute("name")
                    id_attr = await inp.get_attribute("id")
                    is_visible = await inp.is_visible()
                    if is_visible:
                        log.info(
                            "input[%d]: type=%s name=%s id=%s value=%s [VISIBLE]",
                            idx, type_attr, name, id_attr, val
                        )
                except Exception:
                    pass
        except Exception as e:
            log.warning("Fallo en escáner genérico: %s", e)

        log.info("\n=== Resumen de candidatos ===")
        for selector, desc in candidates:
            log.info("  %s (%s)", selector, desc)

        if not keep_open:
            log.info("\nCerrando navegador...")
            await context.close()
        else:
            log.info("\nNavigador abierto. Presiona Ctrl+C para cerrar.")
            try:
                await asyncio.sleep(3600)
            except KeyboardInterrupt:
                pass
            await context.close()


def main():
    parser = argparse.ArgumentParser(description="Inspecciona campo de monto en Pocket Option")
    parser.add_argument("--keep-open", action="store_true", help="Mantener navegador abierto")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.keep_open))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")


if __name__ == "__main__":
    main()
