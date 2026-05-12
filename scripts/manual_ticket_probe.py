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
            source_text="MANUAL_TICKET_PROBE",
            received_at=TradingSignal.now_utc(),
        )

        click_sent_at = ts()
        print(json.dumps({"phase": "sending_order", "at_utc": click_sent_at}, ensure_ascii=True), flush=True)
        await asyncio.wait_for(client.place_order(signal), timeout=120)
        data["click_sent_at_utc"] = click_sent_at
        print(json.dumps({"phase": "order_sent", "at_utc": ts()}, ensure_ascii=True), flush=True)

        after_open_balance = await client.get_account_balance()
        data["after_open_balance"] = after_open_balance
        data["reserved_diff"] = round(after_open_balance - before_balance, 4)

        first_snapshot_at: str | None = None
        first_snapshot_raw: str | None = None
        last_snapshot_at: str | None = None
        first_timer_sec: int | None = None
        min_timer_sec: int | None = None
        max_timer_sec: int | None = None
        close_detected_at: str | None = None
        last_snapshot_raw: str | None = None

        had_snapshot = False

        max_wait_seconds = max(180, expiry_minutes * 60 + 120)
        for idx in range(max_wait_seconds):
            snap = await client.get_live_trade_snapshot(asset, side=None, timeout=0.7)
            now = ts()

            if snap is not None:
                had_snapshot = True
                last_snapshot_at = now
                last_snapshot_raw = snap.raw_text

                if first_snapshot_at is None:
                    first_snapshot_at = now
                    first_snapshot_raw = snap.raw_text
                if snap.time_remaining_sec is not None:
                    if first_timer_sec is None:
                        first_timer_sec = int(snap.time_remaining_sec)
                    if min_timer_sec is None or snap.time_remaining_sec < min_timer_sec:
                        min_timer_sec = int(snap.time_remaining_sec)
                    if max_timer_sec is None or snap.time_remaining_sec > max_timer_sec:
                        max_timer_sec = int(snap.time_remaining_sec)
            else:
                if had_snapshot:
                    close_detected_at = now
                    break

            if idx % 15 == 0:
                print(
                    json.dumps(
                        {
                            "progress_at_utc": now,
                            "had_snapshot": had_snapshot,
                            "timer_sec": (int(snap.time_remaining_sec) if snap and snap.time_remaining_sec is not None else None),
                        },
                        ensure_ascii=True,
                    ),
                    flush=True,
                )

            await asyncio.sleep(1.0)

        final_balance = await client.get_account_balance()
        data["final_balance"] = final_balance
        data["final_diff_vs_before"] = round(final_balance - before_balance, 4)

        data["ticket_probe"] = {
            "first_snapshot_at_utc": first_snapshot_at,
            "first_snapshot_raw": first_snapshot_raw,
            "last_snapshot_at_utc": last_snapshot_at,
            "close_detected_at_utc": close_detected_at,
            "first_timer_sec": first_timer_sec,
            "min_timer_sec": min_timer_sec,
            "max_timer_sec": max_timer_sec,
            "last_snapshot_raw": last_snapshot_raw,
        }

        print(json.dumps(data, ensure_ascii=True, indent=2))

    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(json.dumps({"interrupted": True, "interrupted_at_utc": ts()}, ensure_ascii=True), flush=True)
