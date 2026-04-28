#!/usr/bin/env python3
"""Prueba real en cuenta demo: AUDUSD OTC BUY con martingala.

Flujo:
- prepara asset + expiracion + monto
- hace click BUY en el segundo programado
- monitorea resultado real por balance
- si pierde, entra martingala con los montos definidos
"""

import asyncio
import logging
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import AppSettings
from src.core.engine import SignalEngine
from src.core.models import TradingSignal
from src.pocket_option.client import PocketOptionDemoClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


async def main() -> None:
    settings = AppSettings.load()

    asset = "AUDUSD OTC"
    side = "BUY"
    expiry_minutes = 5
    martingale_amounts = [1.25, 2.50, 5.00]
    start_delay_seconds = 12

    print("\n" + "=" * 90)
    print("PRUEBA REAL DEMO: AUDUSD OTC BUY + martingala")
    print("=" * 90)
    print(f"Activo: {asset}")
    print(f"Direccion: {side}")
    print(f"Expiracion: {expiry_minutes}m")
    print(f"Montos: {martingale_amounts}")
    print(f"Inicio programado en: {start_delay_seconds}s")
    print("Cuenta: DEMO")
    print("=" * 90 + "\n")

    client = PocketOptionDemoClient(
        account_mode=settings.pocket_account_mode,
        default_asset=asset,
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

    engine = SignalEngine(
        pocket_client=client,
        martingale_amounts=martingale_amounts,
        martingale_mode="fixed",
        calc_payout_percent=settings.calc_payout_percent,
        calc_increment=settings.calc_increment,
        calc_rule10_balance_threshold=settings.calc_rule10_balance_threshold,
        calc_max_steps=settings.calc_max_steps,
        result_grace_seconds=settings.order_result_grace_seconds,
        reference_utc_offset_hours=settings.expected_utc_offset_hours,
        color_output=settings.color_output,
        signal_late_tolerance_seconds=settings.signal_late_tolerance_seconds,
    )

    try:
        await client.connect()
        balance = await client.get_account_balance()
        print(f"Saldo demo inicial: {balance:.2f}\n")

        now = TradingSignal.now_utc()
        signal = TradingSignal(
            asset=asset,
            side=side,
            expiry_minutes=expiry_minutes,
            amount=martingale_amounts[0],
            source_text="PRUEBA REAL DEMO MANUAL AUDUSD OTC BUY",
            received_at=now,
            execute_at_utc=now + timedelta(seconds=start_delay_seconds),
            martingale_execute_at_utc=(),
            source_name="TEST_REAL_DEMO",
        )

        print("Se ejecutara usando el engine real del bot.")
        print("Si la primera operacion pierde, el engine lanzara martingala automaticamente.\n")

        await engine.execute_signal(signal)

        final_balance = await client.get_account_balance()
        print(f"\nSaldo demo final: {final_balance:.2f}")

    except Exception as exc:
        logging.exception("Fallo durante prueba real demo: %s", exc)
        print(f"\nERROR: {exc}\n")
        raise
    finally:
        await client.close()
        print("\nCliente cerrado.\n")


if __name__ == "__main__":
    asyncio.run(main())
