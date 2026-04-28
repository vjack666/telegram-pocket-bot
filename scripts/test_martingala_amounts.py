#!/usr/bin/env python3
"""
Script de prueba rápida: inyecta 3 montos secuenciales para ver martingala en acción.
Simula ENTRADA → LOSS → MARTINGALA 1 → LOSS → MARTINGALA 2
"""

import asyncio
import logging
import sys
from pathlib import Path

# Agregar raíz del proyecto al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.pocket_option.client import PocketOptionDemoClient
from src.config.settings import AppSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


async def main():
    print("\n" + "=" * 80)
    print("PRUEBA RÁPIDA: Martingala con 3 montos en EUR JPY OTC")
    print("=" * 80)

    settings = AppSettings.load()

    # Crear cliente
    client = PocketOptionDemoClient(
        account_mode=settings.pocket_account_mode,
        default_asset="EUR JPY OTC",
        profile_dir=settings.pocket_profile_dir,
        headless=settings.pocket_headless,
        execute_orders=settings.pocket_execute_orders,
    )

    try:
        # Conectar
        await client.connect()
        print("\n✓ Cliente conectado\n")

        # Buscar y cambiar a EUR JPY OTC
        print("📌 Buscando EUR JPY OTC...")
        await client.ensure_asset("EUR JPY OTC", max_attempts=3)
        print("✓ Par seleccionado: EUR JPY OTC")
        
        # ESPERAR a que se renderice la interfaz completa
        print("⏳ Esperando a que la interfaz de apuestas se cargue (5 seg)...")
        await asyncio.sleep(5)
        print("✓ Interfaz lista\n")

        # Montos de martingala (ajusta según tu config)
        martingale_amounts = [1.26, 2.52, 5.04]
        step_names = ["ENTRADA", "MARTINGALA 1", "MARTINGALA 2"]

        # Inyectar los 3 montos
        for idx, (step_name, amount) in enumerate(zip(step_names, martingale_amounts), 1):
            print("\n" + "─" * 80)
            print(f"PASO {idx}: {step_name}")
            print(f"Monto esperado: ${amount:.2f}")
            print("─" * 80)

            try:
                await client.prepare_order_for_execution("EUR JPY OTC", amount, 5, max_retries=3)
                print(f"✓ {step_name}: Monto ${amount:.2f} inyectado exitosamente\n")
            except Exception as exc:
                print(f"✗ {step_name}: Fallo al inyectar: {exc}\n")

            if idx < len(martingale_amounts):
                print(f"Esperando 2 segundos antes de siguiente monto...")
                await asyncio.sleep(2)

        print("\n" + "=" * 80)
        print("✓ PRUEBA COMPLETADA: Verifica en la consola que viste los 3 montos")
        print("  - ENTRADA: $1.26")
        print("  - MARTINGALA 1: $2.52")
        print("  - MARTINGALA 2: $5.04")
        print("=" * 80 + "\n")

    except Exception as exc:
        logging.exception("Error durante prueba: %s", exc)
        print(f"\n✗ Error: {exc}\n")

    finally:
        await client.close()
        print("✓ Cliente desconectado\n")


if __name__ == "__main__":
    asyncio.run(main())
