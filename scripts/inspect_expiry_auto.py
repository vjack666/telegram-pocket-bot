import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright

DEMO_URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE_DIR = ".pocket_profile"


def looks_like_timer_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    patterns = [
        r"\bm\s*\d{1,3}\b",
        r"\b\d{1,3}\s*m\b",
        r"\b\d{1,2}:\d{2}(?::\d{2})?\b",
        r"\bmin\b|\bminute\b|\bminutes\b|\bminuto\b|\bminutos\b",
        r"\bexpir",
        r"\btime\b",
    ]
    return any(re.search(p, t) for p in patterns)


async def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    profile_path = str(Path(PROFILE_DIR).resolve())

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(DEMO_URL, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(4)

        # 1) Collect visible elements with timer-like text
        visible_candidates = await page.evaluate(
            """
            () => {
                const out = [];
                const nodes = Array.from(document.querySelectorAll('button,a,span,div,li,p,[role="button"],input'));

                const isVisible = (el) => {
                    const st = getComputedStyle(el);
                    if (!st || st.display === 'none' || st.visibility === 'hidden') return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };

                const cssPath = (el) => {
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
                                part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
                            }
                        }
                        path.unshift(part);
                        node = parent;
                    }
                    return path.join(' > ');
                };

                for (const el of nodes) {
                    if (!isVisible(el)) continue;
                    const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
                    if (!text || text.length > 60) continue;
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        text,
                        id: el.id || null,
                        name: el.getAttribute('name'),
                        role: el.getAttribute('role'),
                        className: (el.className || '').toString(),
                        dataTestId: el.getAttribute('data-testid'),
                        selector: cssPath(el),
                    });
                }
                return out;
            }
            """
        )

        filtered_visible = [x for x in visible_candidates if looks_like_timer_text(x.get("text", ""))]
        log.info("=== visibles con texto de timing (%d) ===", len(filtered_visible))
        for idx, item in enumerate(filtered_visible[:80], start=1):
            log.info("[%d] text='%s' | selector=%s", idx, item.get("text"), item.get("selector"))

        # 2) Class/id/name probe for timer-like attributes (visible or not)
        attr_candidates = await page.evaluate(
            """
            () => {
                const out = [];
                const nodes = Array.from(document.querySelectorAll('*'));
                const cssPath = (el) => {
                    if (!el) return '';
                    if (el.id) return `#${el.id}`;
                    const path = [];
                    let node = el;
                    while (node && node.nodeType === 1 && path.length < 6) {
                        let part = node.tagName.toLowerCase();
                        if (node.classList && node.classList.length) {
                            const cls = Array.from(node.classList).slice(0, 2).join('.');
                            if (cls) part += '.' + cls;
                        }
                        path.unshift(part);
                        node = node.parentElement;
                    }
                    return path.join(' > ');
                };

                for (const el of nodes) {
                    const id = (el.id || '').toLowerCase();
                    const name = (el.getAttribute('name') || '').toLowerCase();
                    const cls = (el.className || '').toString().toLowerCase();
                    const dt = (el.getAttribute('data-testid') || '').toLowerCase();
                    const all = `${id} ${name} ${cls} ${dt}`;
                    if (!/(expir|duration|time|timer)/.test(all)) continue;
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || null,
                        name: el.getAttribute('name'),
                        className: (el.className || '').toString(),
                        dataTestId: el.getAttribute('data-testid'),
                        text: ((el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ')).slice(0, 80),
                        selector: cssPath(el),
                    });
                }
                return out.slice(0, 200);
            }
            """
        )

        log.info("=== atributos relacionados (expir/time) (%d) ===", len(attr_candidates))
        for idx, item in enumerate(attr_candidates[:120], start=1):
            log.info(
                "[%d] tag=%s id=%s name=%s class=%s text='%s' selector=%s",
                idx,
                item.get("tag"),
                item.get("id"),
                item.get("name"),
                item.get("className"),
                item.get("text"),
                item.get("selector"),
            )

        # 3) Attempt click openers and search for 5m options
        opener_selectors = [
            "[class*='expir' i]",
            "[class*='duration' i]",
            "[class*='time' i]",
            "[data-testid*='expir' i]",
            "[data-testid*='time' i]",
            "button:has-text('5m')",
            "button:has-text('M5')",
            "span:has-text('5m')",
            "span:has-text('M5')",
        ]

        log.info("=== probing apertura de panel de expiracion ===")
        findings = []
        for sel in opener_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible():
                    continue
                await loc.click(timeout=1200)
                await asyncio.sleep(0.4)

                option5 = page.locator("text=/^\\s*(5\\s*m|m\\s*5|00:05(:00)?)\\s*$/i")
                count5 = await option5.count()
                findings.append({"opener": sel, "five_options": count5})
                if count5 > 0:
                    log.info("opener=%s => encontro opciones 5m: %d", sel, count5)
                else:
                    log.info("opener=%s => sin opciones 5m visibles", sel)
            except Exception as exc:
                log.info("opener=%s fallo: %s", sel, exc)

        print("\n=== FINDINGS JSON ===")
        print(json.dumps({
            "visible_timing": filtered_visible[:60],
            "attr_candidates": attr_candidates[:120],
            "opener_findings": findings,
        }, ensure_ascii=False, indent=2))

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
