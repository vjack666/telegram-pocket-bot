import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

DEMO_URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE_DIR = str(Path(".pocket_profile").resolve())


async def main() -> None:
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(user_data_dir=PROFILE_DIR, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(DEMO_URL, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(3)

        html = await page.evaluate(
            """
            () => {
              const el = document.querySelector('#put-call-buttons-chart-1 .block--expiration-inputs')
                       || document.querySelector('.block--expiration-inputs');
              if (!el) return '';
              return el.outerHTML;
            }
            """
        )
        print("=== EXPIRY BLOCK HTML ===")
        print(html)

        # Try opening expiry control by clicking the visible value.
        try:
            value = page.locator("#put-call-buttons-chart-1 .block--expiration-inputs .value__val").first
            if await value.count() > 0 and await value.is_visible():
                await value.click(timeout=2000)
                await asyncio.sleep(0.6)
        except Exception as exc:
            print(f"click value failed: {exc}")

        html_after = await page.evaluate(
            """
            () => {
                const el = document.querySelector('#put-call-buttons-chart-1 .block--expiration-inputs')
                         || document.querySelector('.block--expiration-inputs');
                if (!el) return '';
                return el.outerHTML;
            }
            """
        )
        print("=== EXPIRY BLOCK HTML AFTER CLICK ===")
        print(html_after)

        popup_info = await page.evaluate(
            """
            () => {
                const out = [];
                const nodes = Array.from(document.querySelectorAll('.dropdown, [class*=dropdown], [class*=expiration], [class*=expir], [class*=time]'));
                const isVisible = (el) => {
                    const st = getComputedStyle(el);
                    if (!st || st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                for (const el of nodes) {
                    if (!isVisible(el)) continue;
                    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
                    if (!text || text.length > 120) continue;
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        className: (el.className || '').toString(),
                        text,
                    });
                }
                return out.slice(0, 80);
            }
            """
        )
        print("=== VISIBLE POPUP CANDIDATES ===")
        for item in popup_info:
            print(item)

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
