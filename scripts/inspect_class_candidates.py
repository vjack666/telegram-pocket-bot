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
            const wanted = ['buy', 'sell', 'up', 'down', 'call', 'put', 'higher', 'lower', 'amount', 'asset'];
            const nodes = Array.from(document.querySelectorAll('*'));
            const out = [];
            for (const el of nodes) {
                const cls = (el.className || '').toString();
                const id = (el.id || '').toString();
                const blob = (cls + ' ' + id).toLowerCase();
                if (!blob) continue;
                if (!wanted.some(w => blob.includes(w))) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 8) continue;
                out.push({
                    tag: el.tagName,
                    id,
                    cls,
                    txt: (el.textContent || '').trim().slice(0, 80),
                });
            }
            return out.slice(0, 500);
        }
        """

        data = await page.evaluate(js)
        for item in data:
            print(item)

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
