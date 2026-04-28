import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


URL = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
PROFILE = r"c:/Users/v_jac/Desktop/poket option/.pocket_profile"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspecciona candidatos de selectores para el panel de activos de Pocket Option.",
    )
    parser.add_argument(
        "--panel-seconds",
        type=int,
        default=12,
        help="Segundos para que abras manualmente el panel de activos antes de inspeccionar.",
    )
    parser.add_argument(
        "--open-selector",
        default="a.pair-number-wrap",
        help="Si lo pasas, el script intenta abrir el panel automaticamente antes de inspeccionar.",
    )
    return parser


async def main() -> None:
    args = build_parser().parse_args()

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE,
            headless=False,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded")

        print(f"URL={page.url}")
        print(f"TITLE={await page.title()}")

        if args.open_selector.strip():
            try:
                await page.locator(args.open_selector.strip()).first.click(timeout=2500)
                print(f"Panel intentado con open-selector: {args.open_selector.strip()}")
                await asyncio.sleep(2)
            except Exception as exc:
                print(f"No se pudo abrir con open-selector: {exc}")

        print(
            f"\nAbre manualmente el panel de activos ahora. Esperando {args.panel_seconds}s antes de inspeccionar..."
        )
        for remaining in range(args.panel_seconds, 0, -1):
            print(f"Inspeccion en {remaining}s")
            await asyncio.sleep(1)

        data = await page.evaluate(
            r"""
            () => {
                const PAIR_RE = /[A-Z]{3}\/?[A-Z]{3}/;

                const isVisible = (el) => {
                    if (!el || !(el instanceof Element)) return false;
                    const style = window.getComputedStyle(el);
                    if (style.visibility === 'hidden' || style.display === 'none') return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 8 && rect.height > 8;
                };

                const toSelector = (el) => {
                    if (!el) return '';
                    if (el.id) return `#${el.id}`;

                    const stableAttrs = [
                        'data-testid',
                        'data-test',
                        'data-qa',
                        'name',
                        'role',
                        'placeholder',
                        'aria-label',
                        'type'
                    ];
                    for (const attr of stableAttrs) {
                        const value = el.getAttribute && el.getAttribute(attr);
                        if (value) return `[${attr}="${value}"]`;
                    }

                    const parts = [];
                    let node = el;
                    while (node && node.nodeType === 1 && parts.length < 6) {
                        let part = node.tagName.toLowerCase();
                        if (node.classList && node.classList.length) {
                            const cls = Array.from(node.classList)
                                .filter(c => !/^active|selected|open|focus|hover|ng-/.test(c))
                                .slice(0, 3)
                                .join('.');
                            if (cls) part += '.' + cls;
                        }

                        const parent = node.parentElement;
                        if (parent) {
                            const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
                            if (siblings.length > 1) {
                                part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
                            }
                        }

                        parts.unshift(part);
                        node = parent;
                    }
                    return parts.join(' > ');
                };

                const summarize = (el) => ({
                    tag: el.tagName.toLowerCase(),
                    selector: toSelector(el),
                    text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 140),
                    placeholder: el.getAttribute('placeholder') || '',
                    role: el.getAttribute('role') || '',
                    type: el.getAttribute('type') || '',
                    className: (el.className || '').toString().slice(0, 160),
                });

                const summarizePanel = (el) => ({
                    selector: toSelector(el),
                    tag: el.tagName.toLowerCase(),
                    className: (el.className || '').toString().slice(0, 160),
                    text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 200),
                });

                const collectPanelRoots = () => {
                    const nodes = Array.from(document.querySelectorAll('div, section, aside, ul'));
                    const candidates = [];

                    for (const el of nodes) {
                        if (!isVisible(el)) continue;

                        const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!text) continue;

                        const upper = text.toUpperCase();
                        const pairMatches = upper.match(new RegExp(PAIR_RE, 'g')) || [];
                        const otcCount = (upper.match(/OTC/g) || []).length;
                        const cls = ((el.className || '') + ' ' + (el.id || '')).toLowerCase();

                        let score = 0;
                        if (pairMatches.length >= 3) score += pairMatches.length * 3;
                        if (otcCount >= 2) score += otcCount * 4;
                        if (cls.includes('asset') || cls.includes('alist') || cls.includes('currenc')) score += 20;
                        if (el.querySelector('input, [role="textbox"], [role="searchbox"], [role="combobox"]')) score += 25;
                        if (el.querySelector('li, [role="option"], a')) score += 10;
                        if (text.length > 1200) score -= 10;

                        if (score >= 20) {
                            candidates.push({ el, score });
                        }
                    }

                    candidates.sort((a, b) => b.score - a.score);
                    return candidates.slice(0, 5);
                };

                const panelRoots = collectPanelRoots();
                const panelRoot = panelRoots.length > 0 ? panelRoots[0].el : document.body;

                const openCandidates = Array.from(document.querySelectorAll('button, a, [role="button"], div, span'))
                    .filter(isVisible)
                    .filter((el) => {
                        const blob = [
                            el.className || '',
                            el.id || '',
                            el.getAttribute('aria-label') || '',
                            el.textContent || ''
                        ].join(' ').toLowerCase();
                        return ['asset', 'pair', 'currency', 'symbol', 'otc'].some(k => blob.includes(k));
                    })
                    .slice(0, 25)
                    .map(summarize);

                const searchCandidates = Array.from(panelRoot.querySelectorAll('input, textarea, [contenteditable="true"], [role="textbox"], [role="searchbox"], [role="combobox"]'))
                    .filter(isVisible)
                    .slice(0, 20)
                    .map(summarize);

                const resultCandidates = Array.from(panelRoot.querySelectorAll('li, a, button, [role="option"], div'))
                    .filter(isVisible)
                    .filter((el) => {
                        const text = (el.textContent || '').trim();
                        if (!text) return false;
                        const compact = text.replace(/\s+/g, ' ').toUpperCase();
                        return compact.includes('OTC') || PAIR_RE.test(compact);
                    })
                    .slice(0, 40)
                    .map(summarize);

                return {
                    panelCandidates: panelRoots.map(({ el, score }) => ({ score, ...summarizePanel(el) })),
                    openCandidates,
                    searchCandidates,
                    resultCandidates,
                };
            }
            """
        )

        print("\n=== PANEL CANDIDATES ===")
        for item in data["panelCandidates"]:
            print(item)

        print("\n=== OPEN CANDIDATES ===")
        for item in data["openCandidates"]:
            print(item)

        print("\n=== SEARCH CANDIDATES ===")
        for item in data["searchCandidates"]:
            print(item)

        print("\n=== RESULT CANDIDATES ===")
        for item in data["resultCandidates"]:
            print(item)

        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())