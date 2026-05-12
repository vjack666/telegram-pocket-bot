"""
Probe para capturar TODA la información visual disponible en el ticket/panel
cuando se abre una orden, especialmente si contiene hora de cierre.
"""
import asyncio
import json
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
        "mode": settings.pocket_account_mode,
        "execute_orders": settings.pocket_execute_orders,
    }

    try:
        print(json.dumps({"phase": "connecting", "at_utc": ts()}, ensure_ascii=True), flush=True)
        await asyncio.wait_for(client.connect(), timeout=90)
        print(json.dumps({"phase": "connected", "at_utc": ts()}, ensure_ascii=True), flush=True)

        selected_asset = await client.get_selected_asset()
        asset = selected_asset or settings.default_asset
        side = "BUY"
        configured_expiry_seconds = await client.get_configured_expiry_seconds()
        if configured_expiry_seconds is None:
            expiry_minutes = 1
        else:
            expiry_minutes = max(1, int(round(configured_expiry_seconds / 60.0)))
        amount = 1.0

        before_balance = await client.get_account_balance()
        data["asset"] = asset
        data["side"] = side
        data["expiry_minutes"] = expiry_minutes
        data["amount"] = amount
        data["before_balance"] = before_balance

        signal = TradingSignal(
            asset=asset,
            side=side,
            expiry_minutes=expiry_minutes,
            amount=amount,
            source_text="TICKET_INFO_PROBE",
            received_at=TradingSignal.now_utc(),
        )

        click_sent_at = ts()
        print(json.dumps({"phase": "sending_order", "at_utc": click_sent_at}, ensure_ascii=True), flush=True)
        await asyncio.wait_for(client.place_order(signal), timeout=120)
        data["click_sent_at_utc"] = click_sent_at
        print(json.dumps({"phase": "order_sent", "at_utc": ts()}, ensure_ascii=True), flush=True)

        # Ahora capturar snapshots en momentos clave: t=1s, t=3s, t=10s
        snapshots_captured = []
        capture_times = [1, 3, 10]
        
        for capture_at in capture_times:
            await asyncio.sleep(capture_at)
            snap = await client.get_live_trade_snapshot(asset, side=None, timeout=0.7)
            now = ts()
            if snap is not None:
                snapshots_captured.append({
                    "captured_at_utc": now,
                    "seconds_after_open": capture_at,
                    "raw_text": snap.raw_text,
                    "pnl_value": snap.pnl_value,
                    "amount": snap.amount,
                    "time_remaining_sec": snap.time_remaining_sec,
                    "forecast_side": snap.forecast_side,
                    "open_price": snap.open_price,
                    "close_price": snap.close_price,
                })
                print(json.dumps(
                    {
                        "snapshot_at": capture_at,
                        "raw_text": snap.raw_text,
                        "time_remaining_sec": snap.time_remaining_sec,
                    },
                    ensure_ascii=True,
                ), flush=True)
        
        data["snapshots_at_key_moments"] = snapshots_captured

        # También intentar leer raw HTML del panel para ver si hay timestamps o info adicional
        try:
            page = client._page
            if page is not None:
                # Buscar texto que contenga números de horas/minutos
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
                        return texts.slice(0, 10);
                    }
                    """
                )
                data["panel_visible_texts"] = panel_html
        except Exception as e:
            print(json.dumps({"panel_read_error": str(e)}, ensure_ascii=True), flush=True)

        print(json.dumps(data, ensure_ascii=True, indent=2))

    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(json.dumps({"interrupted": True, "interrupted_at_utc": ts()}, ensure_ascii=True), flush=True)
