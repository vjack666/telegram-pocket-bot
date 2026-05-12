"""
Probe para decodificar exactamente qué es ese número que aparece en el panel.
Captura el texto del panel cada segundo y analiza los números.
"""
import asyncio
import json
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.config.settings import AppSettings
from src.core.models import TradingSignal
from src.pocket_option.client import PocketOptionDemoClient


def ts() -> str:
    return datetime.now(timezone.utc).isoformat()


async def main() -> None:
    load_dotenv()
    settings = AppSettings.load()

    client = PocketOptionDemoClient(
        account_mode=settings.pocket_account_mode,
        default_asset=settings.default_asset,
        demo_url=settings.pocket_demo_url,
        profile_dir=settings.pocket_profile_dir,
        headless=settings.pocket_headless,
        execute_orders=settings.pocket_execute_orders,
        max_order_amount=settings.pocket_max_order_amount,
        balance_selector=settings.pocket_balance_selector,
        asset_open_selector=settings.pocket_asset_open_selector,
        asset_search_selector=settings.pocket_asset_search_selector,
        asset_result_selector=settings.pocket_asset_result_selector,
        buy_selector=settings.pocket_buy_selector,
        sell_selector=settings.pocket_sell_selector,
        amount_selector=settings.pocket_amount_selector,
    )

    data: dict[str, object] = {
        "started_at_utc": ts(),
        "goal": "Decodificar número de cierre en panel y verificar si es time_remaining",
    }

    try:
        print(json.dumps({"phase": "connecting", "at_utc": ts()}, ensure_ascii=True), flush=True)
        await asyncio.wait_for(client.connect(), timeout=90)
        print(json.dumps({"phase": "connected", "at_utc": ts()}, ensure_ascii=True), flush=True)

        selected_asset = await client.get_selected_asset()
        asset = selected_asset or settings.default_asset
        
        configured_expiry_seconds = await client.get_configured_expiry_seconds()
        expiry_minutes = max(1, int(round(configured_expiry_seconds / 60.0))) if configured_expiry_seconds else 1

        signal = TradingSignal(
            asset=asset,
            side="BUY",
            expiry_minutes=expiry_minutes,
            amount=1.0,
            source_text="TIMER_DECODE_PROBE",
            received_at=TradingSignal.now_utc(),
        )

        click_sent_at = ts()
        print(json.dumps({"phase": "sending_order", "at_utc": click_sent_at}, ensure_ascii=True), flush=True)
        await asyncio.wait_for(client.place_order(signal), timeout=120)
        print(json.dumps({"phase": "order_sent", "at_utc": ts()}, ensure_ascii=True), flush=True)

        # Capturar información del panel cada segundo durante 20 segundos
        readings = []
        for sec in range(1, 21):
            await asyncio.sleep(1.0)
            
            try:
                page = client._page
                if page is not None:
                    # Usar la MISMA lógica que funcionó en ticket_info_probe.py
                    panel_html = await page.evaluate(
                        """
                        () => {
                            // Buscar todos los textos del panel de trades
                            const selectors = [
                                '[class*="trades"]',
                                '[class*="deals"]',
                                '[class*="opened"]',
                                '[class*="panel"]',
                            ];
                            
                            const texts = [];
                            for (const sel of selectors) {
                                const els = document.querySelectorAll(sel);
                                for (const el of els) {
                                    if (el.offsetHeight > 0 && el.offsetWidth > 0) {
                                        const txt = el.textContent || '';
                                        if (txt.length > 10 && txt.length < 500) {
                                            texts.push(txt.replace(/\\s+/g, ' ').trim());
                                        }
                                    }
                                }
                            }
                            return texts.slice(0, 15);
                        }
                        """
                    )
                    
                    # Filtrar solo textos con OTC
                    otc_texts = [t for t in panel_html if 'OTC' in t]
                    
                    # Extraer números tipo MM:SS
                    timer_numbers = []
                    for text in otc_texts:
                        matches = re.findall(r'\d{1,2}:\d{2}', text)
                        timer_numbers.extend(matches)
                    
                    readings.append({
                        "second": sec,
                        "captured_at_utc": ts(),
                        "otc_panel_texts": otc_texts,
                        "timer_numbers": timer_numbers,
                    })
                    
                    if timer_numbers or sec % 5 == 0:
                        print(json.dumps({
                            "second": sec,
                            "timer_numbers": timer_numbers,
                            "sample_text": otc_texts[0] if otc_texts else None,
                        }, ensure_ascii=True), flush=True)
            except Exception as e:
                print(json.dumps({"error": str(e), "second": sec}, ensure_ascii=True), flush=True)

        data["readings"] = readings
        
        # Analizar progresión de números
        if len(readings) >= 2:
            first_text = readings[0].get("full_text", "")
            last_text = readings[-1].get("full_text", "")
            data["first_panel_text"] = first_text
            data["last_panel_text"] = last_text
            data["analysis"] = "Ver progresión de números en 'readings' - si bajan cada segundo, es timer real"

        print(json.dumps(data, ensure_ascii=True, indent=2))

    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(json.dumps({"interrupted": True}, ensure_ascii=True), flush=True)
