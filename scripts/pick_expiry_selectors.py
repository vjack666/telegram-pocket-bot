import asyncio
from typing import Optional

from playwright.async_api import async_playwright


URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE = r"c:/Users/v_jac/Desktop/poket option/.pocket_profile"


async def pick(page, label: str) -> str:
    print(f"\nHaz click en el elemento para: {label}")
    print("Despues del click, vuelve a esta terminal y presiona Enter.")

    await page.evaluate(
        """
        (label) => {
            window.__pickedSelector = null;

            const toSelector = (el) => {
                if (!el) return '';
                if (el.id) return `#${el.id}`;

                const path = [];
                let node = el;
                while (node && node.nodeType === 1 && path.length < 7) {
                    let part = node.tagName.toLowerCase();
                    if (node.classList && node.classList.length) {
                        const cls = Array.from(node.classList).slice(0, 3).join('.');
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
                const target = e.target;
                window.__pickedSelector = toSelector(target);
                document.removeEventListener('click', handler, true);
            };

            document.addEventListener('click', handler, true);
            console.log('Selector picker armado para: ' + label);
        }
        """,
        label,
    )

    await asyncio.to_thread(input)
    selector: Optional[str] = await page.evaluate("() => window.__pickedSelector")
    if not selector:
        raise RuntimeError(f"No se capturo selector para {label}")
    return selector


async def main() -> None:
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            headless=False,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded")

        expiry_open = await pick(page, "ABRIR CONTROL DE EXPIRACION (timer)")
        expiry_5m = await pick(page, "OPCION EXPIRACION 5M")

        print("\nSelectores capturados:")
        print(f"POCKET_EXPIRY_OPEN_SELECTOR={expiry_open}")
        print(f"POCKET_EXPIRY_5M_SELECTOR={expiry_5m}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
