import asyncio
from typing import Optional

from playwright.async_api import async_playwright


URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE = r"c:/Users/v_jac/Desktop/poket option/.pocket_profile"


async def pick(page, label: str, mode: str) -> str:
    print(f"\nHaz click en el elemento para: {label}")
    print("Despues del click, vuelve a esta terminal y presiona Enter.")

    await page.evaluate(
        """
        ({ label, mode }) => {
            window.__pickedSelector = null;
            window.__pickedMeta = null;

            const EDITABLE_SELECTOR = [
                'input',
                'textarea',
                '[contenteditable="true"]',
                '[role="searchbox"]',
                '[role="textbox"]',
                '[role="combobox"]'
            ].join(', ');

            const isEditable = (el) => {
                if (!el || !el.matches) return false;
                return el.matches(EDITABLE_SELECTOR);
            };

            const chooseTarget = (target, mode) => {
                if (!target) return null;

                if (mode === 'search') {
                    const active = document.activeElement;
                    if (isEditable(active)) return active;

                    const editableParent = target.closest(EDITABLE_SELECTOR);
                    if (editableParent) return editableParent;

                    const editableChild = target.querySelector && target.querySelector(EDITABLE_SELECTOR);
                    if (editableChild) return editableChild;
                }

                if (mode === 'result') {
                    return (
                        target.closest('[role="option"]') ||
                        target.closest('li') ||
                        target.closest('a') ||
                        target.closest('button') ||
                        target
                    );
                }

                if (mode === 'open') {
                    return (
                        target.closest('button') ||
                        target.closest('a') ||
                        target.closest('[role="button"]') ||
                        target.closest('[class*="pair"]') ||
                        target.closest('[class*="asset"]') ||
                        target
                    );
                }

                return target;
            };

            const toSelector = (el) => {
                if (!el) return '';
                if (el.id) return `#${el.id}`;

                const stableAttr = [
                    'data-testid',
                    'data-test',
                    'data-qa',
                    'name',
                    'role',
                    'placeholder',
                    'aria-label'
                ];
                for (const attr of stableAttr) {
                    const value = el.getAttribute && el.getAttribute(attr);
                    if (value) return `[${attr}="${value}"]`;
                }

                const path = [];
                let node = el;
                while (node && node.nodeType === 1 && path.length < 6) {
                    let part = node.tagName.toLowerCase();
                    if (node.classList && node.classList.length) {
                        const cls = Array.from(node.classList)
                            .filter(c => !/^active|selected|open|focus|hover|ng-/.test(c))
                            .slice(0, 2)
                            .join('.');
                        if (cls) part += '.' + cls;
                    }

                    const parent = node.parentElement;
                    if (parent) {
                        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
                        if (siblings.length > 1) {
                            const index = siblings.indexOf(node) + 1;
                            part += `:nth-of-type(${index})`;
                        }
                    }

                    path.unshift(part);
                    node = parent;
                }
                return path.join(' > ');
            };

            const handler = (e) => {
                e.preventDefault();
                e.stopPropagation();
                const target = chooseTarget(e.target, mode);
                window.__pickedSelector = toSelector(target);
                window.__pickedMeta = {
                    tag: target?.tagName || '',
                    text: (target?.textContent || '').trim().slice(0, 120),
                    mode,
                };
                document.removeEventListener('click', handler, true);
            };

            document.addEventListener('click', handler, true);
            console.log('Selector picker armado para: ' + label);
        }
        """,
        {"label": label, "mode": mode},
    )

    await asyncio.to_thread(input)
    selector: Optional[str] = await page.evaluate("() => window.__pickedSelector")
    meta = await page.evaluate("() => window.__pickedMeta")
    if not selector:
        raise RuntimeError(f"No se capturo selector para {label}")
    if meta:
        print(f"Capturado [{meta.get('mode')}]: tag={meta.get('tag')} texto={meta.get('text')}")
    return selector


async def validate_selector(page, selector: str, label: str) -> None:
    locator = page.locator(selector)
    count = await locator.count()
    if count == 0:
        raise RuntimeError(f"Selector invalido para {label}: no encuentra elementos -> {selector}")


async def main() -> None:
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            headless=False,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded")

        print("\n1. Captura el boton que abre la lista de activos.")
        asset_open = await pick(page, "ABRIR LISTA DE ACTIVOS", "open")
        await validate_selector(page, asset_open, "abrir activos")

        print("\n2. Abre manualmente la lista de activos si hace falta.")
        print("3. Captura el input de busqueda dentro del panel de activos.")
        asset_search = await pick(page, "BUSCADOR DE ACTIVOS", "search")
        await validate_selector(page, asset_search, "buscador")

        print("\n4. Busca cualquier activo OTC y captura un resultado de la lista.")
        asset_result = await pick(page, "ITEM DE RESULTADO DE ACTIVO", "result")
        await validate_selector(page, asset_result, "resultado")

        if asset_open == asset_search:
            raise RuntimeError(
                "El selector de apertura y el selector de busqueda quedaron iguales. "
                "El buscador no se capturo correctamente; vuelve a ejecutar el script y haz click dentro del input real."
            )

        print("\nSelectores capturados:")
        print(f"POCKET_ASSET_OPEN_SELECTOR={asset_open}")
        print(f"POCKET_ASSET_SEARCH_SELECTOR={asset_search}")
        print(f"POCKET_ASSET_RESULT_SELECTOR={asset_result}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())