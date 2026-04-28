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
            const wanted = ['buy', 'sell', 'up', 'down', 'call', 'put', 'higher', 'lower'];
            const nodes = Array.from(document.querySelectorAll('button, a, div, span'));
            const out = [];
            for (const el of nodes) {
                const txt = (el.textContent || '').trim();
                if (!txt || txt.length > 60) continue;
                const low = txt.toLowerCase();
                if (!wanted.some(w => low.includes(w))) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 10) continue;
                const style = window.getComputedStyle(el);
                const clickable = el.tagName === 'BUTTON' || style.cursor === 'pointer' || !!el.onclick;
                if (!clickable) continue;
                out.push({
                    tag: el.tagName,
                    id: el.id || '',
                    cls: el.className || '',
                    txt,
                    aria: el.getAttribute('aria-label') || '',
                });
            }
            return out.slice(0, 200);
        }
        """

        data = await page.evaluate(js)
        for item in data:
            print(item)

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
