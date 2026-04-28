"""
inspect_expiry_field.py
=======================
Inspecciona el DOM para ubicar el control de expiracion/timing (M1, M5, 00:05:00, etc.).

Uso:
    python scripts/inspect_expiry_field.py
    python scripts/inspect_expiry_field.py --keep-open
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

DEMO_URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE_DIR = ".pocket_profile"


def _looks_like_expiry_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    patterns = [
        r"\bm\s*\d{1,3}\b",
        r"\b\d{1,3}\s*m\b",
        r"\b\d{1,2}:\d{2}(?::\d{2})?\b",
        r"\bmin(?:ute|utos?)?\b",
        r"\bexpiry\b|\bexpir",
    ]
    return any(re.search(p, t) for p in patterns)


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

        log.info("Navegador abierto. Esperando 3 segundos...")
        await asyncio.sleep(3)

        log.info("\n=== Candidatos directos (inputs/selects de tiempo) ===\n")
        direct_selectors = [
            "input[name*='time' i]",
            "input[name*='expir' i]",
            "input[id*='time' i]",
            "input[id*='expir' i]",
            "select[name*='time' i]",
            "select[name*='expir' i]",
            "[class*='time' i] input",
            "[class*='expir' i] input",
            "[class*='duration' i] input",
        ]

        found_direct = 0
        for selector in direct_selectors:
            try:
                nodes = await page.query_selector_all(selector)
                for idx, node in enumerate(nodes[:5]):
                    visible = await node.is_visible()
                    if not visible:
                        continue
                    val = await node.get_attribute("value")
                    name = await node.get_attribute("name")
                    node_id = await node.get_attribute("id")
                    node_type = await node.get_attribute("type")
                    log.info(
                        "Directo %s[%d]: visible=%s type=%s name=%s id=%s value=%s",
                        selector,
                        idx,
                        visible,
                        node_type,
                        name,
                        node_id,
                        val,
                    )
                    found_direct += 1
            except Exception as exc:
                log.warning("Fallo selector %s: %s", selector, exc)

        if found_direct == 0:
            log.info("No se detectaron inputs/selects directos visibles de tiempo.")

        log.info("\n=== Candidatos por texto visible (botones/labels) ===\n")

        candidates = await page.evaluate(
            """
            () => {
              function isVisible(el) {
                const st = window.getComputedStyle(el);
                if (!st) return false;
                if (st.visibility === 'hidden' || st.display === 'none') return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
              }

              const nodes = Array.from(document.querySelectorAll('button, a, span, div, li, p, [role="button"]'));
              const out = [];
              for (const el of nodes) {
                if (!isVisible(el)) continue;
                const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
                if (!text || text.length > 40) continue;

                const lower = text.toLowerCase();
                const looksLikeTime = /\bm\s*\d{1,3}\b|\b\d{1,3}\s*m\b|\b\d{1,2}:\d{2}(?::\d{2})?\b|\bmin(?:ute|utos?)?\b|\bexpiry\b|\bexpir/.test(lower);
                if (!looksLikeTime) continue;

                out.push({
                  tag: el.tagName.toLowerCase(),
                  text,
                  id: el.id || null,
                  classes: (el.className || '').toString().trim(),
                  role: el.getAttribute('role'),
                  name: el.getAttribute('name'),
                  dataTestId: el.getAttribute('data-testid'),
                });
              }
              return out.slice(0, 80);
            }
            """
        )

        filtered = []
        for item in candidates:
            if _looks_like_expiry_text(item.get("text", "")):
                filtered.append(item)

        if not filtered:
            log.info("No se detectaron candidatos por texto visible.")
        else:
            for idx, item in enumerate(filtered, start=1):
                log.info(
                    "[%d] tag=%s text='%s' id=%s class=%s role=%s name=%s data-testid=%s",
                    idx,
                    item.get("tag"),
                    item.get("text"),
                    item.get("id"),
                    item.get("classes"),
                    item.get("role"),
                    item.get("name"),
                    item.get("dataTestId"),
                )

        log.info("\n=== JSON candidatos (copiar/pegar) ===\n%s", json.dumps(filtered, ensure_ascii=False, indent=2))

        if not keep_open:
            log.info("\nCerrando navegador...")
            await context.close()
        else:
            log.info("\nNavegador abierto. Presiona Ctrl+C para cerrar.")
            try:
                await asyncio.sleep(3600)
            except KeyboardInterrupt:
                pass
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspecciona campo de expiracion/timing en Pocket Option")
    parser.add_argument("--keep-open", action="store_true", help="Mantener navegador abierto")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.keep_open))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")


if __name__ == "__main__":
    main()
