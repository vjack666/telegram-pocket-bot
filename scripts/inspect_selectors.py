import asyncio
from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=r"c:/Users/v_jac/Desktop/poket option/.pocket_profile",
            headless=False,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(
            "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
            wait_until="domcontentloaded",
        )

        js = """
        () => {
            const els = Array.from(document.querySelectorAll('input, [contenteditable="true"], button, [role="button"]'));
            return els.slice(0, 1000).map(e => ({
                tag: e.tagName || '',
                type: e.getAttribute('type') || '',
                id: e.id || '',
                cls: e.className || '',
                name: e.getAttribute('name') || '',
                aria: e.getAttribute('aria-label') || '',
                ph: e.getAttribute('placeholder') || '',
                txt: (e.innerText || '').trim().slice(0, 100)
            }));
        }
        """
        data = await page.evaluate(js)

        keys = [
            "amount",
            "monto",
            "investment",
            "stake",
            "buy",
            "sell",
            "up",
            "down",
            "call",
            "put",
            "otc",
            "asset",
        ]

        for item in data:
            blob = " ".join(str(item.get(k, "")) for k in ["tag", "type", "id", "cls", "name", "aria", "ph", "txt"]).lower()
            if any(k in blob for k in keys):
                print(item)

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
