import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from playwright.async_api import async_playwright
from playwright.async_api import Error as PlaywrightError

from src.core.models import TradingSignal
from src.pocket_option.assets import canonicalize_pocket_asset
from src.pocket_option.candle_feed import CandleFeed
from src.pocket_option.trade_panel_feed import LiveTradeSnapshot, TradePanelFeed

# Para los métodos de timing
import time as time_module


ASSET_MODAL_SELECTOR = ".drop-down-modal--quotes-list"


class PocketOptionBaseClient(ABC):
    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_account_balance(self) -> float:
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, signal: TradingSignal) -> None:
        raise NotImplementedError

    async def get_live_price(self, asset: str, timeout: float = 2.0) -> float | None:
        raise NotImplementedError

    async def get_live_trade_snapshot(
        self,
        asset: str,
        side: str,
        timeout: float = 1.5,
    ) -> LiveTradeSnapshot | None:
        raise NotImplementedError

    async def prepare_order_for_execution(
        self,
        asset: str,
        amount: float,
        expiry_minutes: int = 1,
        max_retries: int = 3,
    ) -> None:
        raise NotImplementedError

    async def execute_order_click(self, side: str) -> None:
        raise NotImplementedError

    async def get_selected_asset(self) -> str:
        return ""


class PocketOptionDemoClient(PocketOptionBaseClient):
    def __init__(
        self,
        account_mode: str = "demo",
        default_asset: str = "EURUSD",
        demo_url: str = "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        profile_dir: str = ".pocket_profile",
        headless: bool = False,
        execute_orders: bool = False,
        max_order_amount: float = 5.0,
        balance_selector: str = "",
        asset_open_selector: str = "",
        asset_search_selector: str = "",
        asset_result_selector: str = "",
        buy_selector: str = "",
        sell_selector: str = "",
        amount_selector: str = "",
    ) -> None:
        self._account_mode = account_mode
        self._default_asset = default_asset
        self._demo_url = demo_url
        self._profile_dir = profile_dir
        self._headless = headless
        self._execute_orders = execute_orders
        self._max_order_amount = max_order_amount
        self._balance_selector = balance_selector.strip()
        self._asset_open_selector = asset_open_selector.strip()
        self._asset_search_selector = asset_search_selector.strip()
        self._asset_result_selector = asset_result_selector.strip()
        self._buy_selector = buy_selector.strip()
        self._sell_selector = sell_selector.strip()
        self._amount_selector = amount_selector.strip()
        self._playwright = None
        self._context = None
        self._page = None
        self._state_lock = asyncio.Lock()
        self._browser_ui_lock = asyncio.Lock()  # serializa operaciones de UI (place_order)
        self._browser_ui_lock_timeout_seconds = 30.0
        self._is_starting = False
        self._is_running = False
        self._is_closing = False
        self._active_playwright_ops = 0
        self._last_selected_asset = ""
        self._candle_feed = CandleFeed()
        self._trade_panel_feed = TradePanelFeed()

    async def connect(self) -> None:
        async with self._state_lock:
            if self._context is not None and self._page is not None:
                self._is_running = True
                return

            if self._is_closing:
                logging.info("connect() omitido: cierre de navegador en progreso")
                return

            self._is_starting = True
            self._active_playwright_ops += 1
            profile_path = str(Path(self._profile_dir).resolve())
            max_profile_open_retries = 8
            profile_open_retry_delay_seconds = 2.0

            try:
                last_profile_exc: PlaywrightError | None = None
                for attempt in range(1, max_profile_open_retries + 1):
                    self._playwright = await async_playwright().start()
                    try:
                        self._context = await self._playwright.chromium.launch_persistent_context(
                            user_data_dir=profile_path,
                            headless=self._headless,
                            args=["--disable-blink-features=AutomationControlled"],
                        )
                        break
                    except PlaywrightError as exc:
                        last_profile_exc = exc
                        await self._playwright.stop()
                        self._playwright = None

                        if _is_profile_in_use_error(exc) and attempt < max_profile_open_retries:
                            logging.warning(
                                "Perfil de navegador en uso (intento %s/%s). Reintentando en %.1fs...",
                                attempt,
                                max_profile_open_retries,
                                profile_open_retry_delay_seconds,
                            )
                            await asyncio.sleep(profile_open_retry_delay_seconds)
                            continue

                        raise RuntimeError(
                            "No se pudo abrir el perfil del navegador. Cierra otras ventanas de Pocket Option "
                            "o cualquier Chrome/Chromium que use ese mismo perfil y vuelve a intentar."
                        ) from exc

                if self._context is None:
                    raise RuntimeError(
                        "No se pudo abrir el perfil del navegador tras varios reintentos. "
                        "Verifica que no haya otra instancia usando el mismo perfil."
                    ) from last_profile_exc

                self._page = (
                    self._context.pages[0] if self._context.pages else await self._context.new_page()
                )
                await self._apply_stealth_basics()
                await self._goto_with_retries(
                    self._demo_url,
                    max_attempts=4,
                    timeout_ms=60000,
                )
                self._candle_feed.attach(self._page)
                self._is_running = True
            except PlaywrightError as exc:
                if _is_target_closed_error(exc):
                    logging.info("TargetClosedError durante arranque; probablemente cierre en progreso")
                    return
                raise
            finally:
                self._active_playwright_ops = max(0, self._active_playwright_ops - 1)
                self._is_starting = False

    async def _apply_stealth_basics(self) -> None:
        if self._page is None:
            return
        try:
            await self._page.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                """
            )
        except Exception as exc:
            logging.debug("No se pudo aplicar script stealth basico: %s", exc)

    async def _goto_with_retries(
        self,
        url: str,
        max_attempts: int = 4,
        timeout_ms: int = 60000,
    ) -> None:
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        attempts = max(1, max_attempts)
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                await self._page.goto(url, wait_until="load", timeout=timeout_ms)
                return
            except PlaywrightError as exc:
                last_exc = exc
                if _is_target_closed_error(exc):
                    raise
                if not _is_transient_navigation_error(exc) or attempt >= attempts:
                    raise

                backoff_seconds = min(8.0, float(attempt) * 2.0)
                logging.warning(
                    "Navegacion fallida (%s/%s): %s. Reintentando en %.1fs...",
                    attempt,
                    attempts,
                    exc,
                    backoff_seconds,
                )
                await asyncio.sleep(backoff_seconds)

        raise RuntimeError(f"No se pudo abrir {url}: {last_exc}")

    async def close(self) -> None:
        async with self._state_lock:
            if self._is_closing:
                return

            self._is_closing = True
            logging.info(
                "Cerrando navegador (estado=start:%s run:%s close:%s, tareas_activas=%s)",
                self._is_starting,
                self._is_running,
                self._is_closing,
                self._active_playwright_ops,
            )

            if self._context is not None:
                try:
                    await self._context.close()
                except PlaywrightError as exc:
                    if _is_target_closed_error(exc):
                        logging.info("TargetClosedError ignorado durante shutdown")
                    else:
                        logging.warning("Cierre de contexto ignorado: %s", exc)
                except Exception as exc:
                    if "connection closed" in str(exc).lower():
                        logging.debug("Cierre de contexto ya cerrado: %s", exc)
                    else:
                        logging.warning("Cierre de contexto ignorado: %s", exc)
                finally:
                    self._context = None
                    self._page = None

            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except PlaywrightError as exc:
                    if _is_target_closed_error(exc):
                        logging.info("TargetClosedError ignorado al detener Playwright")
                    else:
                        logging.warning("Cierre de Playwright ignorado: %s", exc)
                except Exception as exc:
                    logging.warning("Cierre de Playwright ignorado: %s", exc)
                finally:
                    self._playwright = None

            self._is_running = False
            self._is_starting = False
            self._is_closing = False

    async def get_account_balance(self) -> float:
        await self.connect()
        return await self._read_balance_from_page()

    async def get_live_price(self, asset: str, timeout: float = 2.0) -> float | None:
        await self.connect()
        target = canonicalize_pocket_asset(asset, default_asset=self._default_asset).strip()
        if not target:
            return None

        tick = self._candle_feed.last_tick(target)
        if tick is not None:
            return tick.price

        tick = await self._candle_feed.wait_tick(target, timeout=timeout)
        if tick is None:
            return None
        return tick.price

    async def get_live_trade_snapshot(
        self,
        asset: str,
        side: str,
        timeout: float = 1.5,
    ) -> LiveTradeSnapshot | None:
        await self.connect()
        if self._page is None:
            return None

        target = canonicalize_pocket_asset(asset, default_asset=self._default_asset).strip()
        if not target:
            return None

        deadline = time.monotonic() + max(0.2, timeout)
        while time.monotonic() < deadline:
            try:
                snapshot = await self._trade_panel_feed.read_live_snapshot(
                    self._page,
                    target,
                    side,
                )
            except Exception as exc:
                logging.debug("No se pudo leer snapshot vivo de Trades: %s", exc)
                snapshot = None
            if snapshot is not None:
                return snapshot
            await asyncio.sleep(0.2)
        return None

    def lifecycle_snapshot(self) -> dict[str, bool | int]:
        return {
            "is_starting": self._is_starting,
            "is_running": self._is_running,
            "is_closing": self._is_closing,
            "active_playwright_ops": self._active_playwright_ops,
            "has_playwright": self._playwright is not None,
            "has_context": self._context is not None,
            "has_page": self._page is not None,
        }

    async def place_order(self, signal: TradingSignal) -> None:
        """Backward compatibility: prepara + ejecuta orden de una vez.
        Para timing preciso, usar prepare_order_for_execution() + execute_order_click() por separado.
        """
        if signal.amount > self._max_order_amount:
            raise RuntimeError(
                f"Orden bloqueada: amount={signal.amount} supera maximo permitido={self._max_order_amount}"
            )

        payload = {
            "account_mode": self._account_mode,
            "asset": signal.asset or self._default_asset,
            "side": signal.side,
            "expiry_minutes": signal.expiry_minutes,
            "amount": signal.amount,
            "received_at": signal.received_at.isoformat(),
        }
        await self.connect()

        if not self._execute_orders:
            logging.info("[DEMO] Orden preparada para Pocket Option (simulada): %s", payload)
            return

        async def _do_place() -> None:
            await self.ensure_asset(payload["asset"], max_attempts=3)
            await self._set_expiry_minutes(signal.expiry_minutes, max_retries=3)
            await self._set_amount(signal.amount, max_retries=3)
            await self._click_side(signal.side, max_retries=3)

        await self._run_with_browser_ui_lock("place_order", _do_place)
        logging.info("Orden enviada a Pocket Option: %s", payload)

    async def prepare_order_for_execution(
        self,
        asset: str,
        amount: float,
        expiry_minutes: int = 1,
        max_retries: int = 3,
    ) -> None:
        """Fase 1: setup durante el countdown (cambiar asset + poner monto).
        Llamar durante _run_countdown() para que en segundo 0 solo se clickee.
        """
        if not self._execute_orders:
            return

        logging.info(
            "━━━ Preparando orden: %s | Exp: %sm | Monto: $%.2f ━━━",
            asset,
            expiry_minutes,
            amount,
        )
        async def _do_prepare() -> None:
            await self.ensure_asset(asset, max_attempts=max_retries)
            await self._set_expiry_minutes(expiry_minutes, max_retries=max_retries)
            await self._set_amount(amount, max_retries=max_retries)

        await self._run_with_browser_ui_lock("prepare_order_for_execution", _do_prepare)
        logging.info(
            "✓ Orden lista: asset=%s exp=%sm monto=$%.2f (listo para clickear)",
            asset,
            expiry_minutes,
            amount,
        )

    async def execute_order_click(self, side: str) -> None:
        """Fase 2: ejecutar orden con un click instantáneo en segundo 0.
        Llamar cuando timestamp llega a hh:mm:00.
        """
        if not self._execute_orders:
            return

        async def _do_click() -> None:
            await self._click_side(side, max_retries=3)

        await self._run_with_browser_ui_lock("execute_order_click", _do_click)
        logging.info("Click de orden ejecutado: %s", side)

    async def ensure_asset(self, asset: str, max_attempts: int = 3) -> None:
        target = canonicalize_pocket_asset(asset, default_asset=self._default_asset).strip()
        if not target:
            return

        attempts = max(1, min(max_attempts, 5))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                await self._ensure_asset_once(target)
                self._last_selected_asset = target
                return
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "ensure_asset intento %s/%s fallo para %s: %s",
                    attempt,
                    attempts,
                    target,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(0.8)

        raise RuntimeError(f"No se pudo asegurar activo {target}: {last_error}")

    async def get_selected_asset(self) -> str:
        if self._page is None:
            return ""

        async def _do_get_selected_asset() -> str:
            current_asset = await self._get_current_asset()
            if current_asset:
                self._last_selected_asset = current_asset
                return current_asset
            return self._last_selected_asset

        return await self._run_with_browser_ui_lock(
            "get_selected_asset",
            _do_get_selected_asset,
        )

    async def _run_with_browser_ui_lock(
        self,
        operation_name: str,
        action: Callable[[], Awaitable],
    ):
        acquired = False
        try:
            await asyncio.wait_for(
                self._browser_ui_lock.acquire(),
                timeout=self._browser_ui_lock_timeout_seconds,
            )
            acquired = True
            return await action()
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Timeout esperando lock UI para {operation_name} ({self._browser_ui_lock_timeout_seconds:.1f}s)"
            ) from exc
        finally:
            if acquired:
                self._browser_ui_lock.release()

    async def _ensure_asset_once(self, target: str) -> None:
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        current_asset = await self._get_current_asset()
        logging.info("Activo actual detectado antes del cambio: %s", current_asset or "desconocido")
        if not current_asset and self._last_selected_asset:
            logging.info("Usando ultimo activo cacheado: %s", self._last_selected_asset)
            current_asset = self._last_selected_asset
        if current_asset and _asset_selection_matches(current_asset, target):
            logging.info("Activo ya seleccionado: %s", current_asset)
            self._last_selected_asset = target
            return

        logging.info("Intentando cambiar a: %s", target)
        logging.info(
            "Cambiando activo: %s -> %s",
            current_asset or "desconocido",
            target,
        )

        await self._open_asset_panel()
        try:
            search = await self._resolve_asset_search_locator()
            await search.click(timeout=2500)

            query = _search_query_for_asset(target)
            logging.info("Escribiendo par normalizado en buscador: %s", query)
            await search.press("Control+A")
            await search.press("Backspace")
            await search.type(query, delay=50, timeout=3500)

            results = await self._resolve_asset_results_locator()
            await results.first.wait_for(state="visible", timeout=5000)

            if "{asset}" in self._asset_result_selector:
                selector = self._asset_result_selector.format(asset=target)
                option = self._page.locator(selector).first
                if await option.count() > 0:
                    await self._click_asset_result(option)
                    await self._verify_asset_changed(target)
                    self._last_selected_asset = target
                    await self._close_asset_panel()
                    return

            try:
                if await self._select_asset_from_results(target):
                    await self._verify_asset_changed(target)
                    self._last_selected_asset = target
                    await self._close_asset_panel()
                    return
            except RuntimeError as exc:
                # CRITICAL FIX: Propagate "asset not available" errors immediately
                if "ACTIVO_NO_DISPONIBLE" in str(exc):
                    raise
                raise

            raise RuntimeError(f"No se encontro el activo solicitado: {target}")
        finally:
            await self._close_asset_panel()

    async def _close_asset_panel(self) -> None:
        if self._page is None:
            return

        modal = self._page.locator(ASSET_MODAL_SELECTOR).first
        try:
            if await modal.is_visible():
                await self._page.keyboard.press("Escape")
                try:
                    await modal.wait_for(state="hidden", timeout=1200)
                    logging.info("Panel de activos cerrado con Escape")
                except Exception:
                    logging.info("Panel de activos sigue visible despues de Escape")
        except Exception:
            return

    async def _open_asset_panel(self) -> None:
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        open_selectors = [
            self._asset_open_selector,
            "a.pair-number-wrap",
            "span.current-symbol.current-symbol_cropped",
        ]

        last_error: Exception | None = None
        for selector in open_selectors:
            if not selector:
                continue
            try:
                locator = self._page.locator(selector).first
                await locator.wait_for(state="visible", timeout=4000)
                await locator.click(timeout=2500)
                await self._page.locator(ASSET_MODAL_SELECTOR).first.wait_for(
                    state="visible",
                    timeout=5000,
                )
                logging.info("Panel de activos abierto con selector: %s", selector)
                return
            except Exception as exc:
                last_error = exc
                logging.info("No se pudo abrir panel con selector %s: %s", selector, exc)

        try:
            opened = await self._page.evaluate(
                f"""
                () => {{
                    const modalSelector = {ASSET_MODAL_SELECTOR!r};
                    const trigger = document.querySelector('a.pair-number-wrap')
                        || document.querySelector('span.current-symbol.current-symbol_cropped')?.closest('a')
                        || document.querySelector('span.current-symbol.current-symbol_cropped');
                    if (!trigger) return false;
                    trigger.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
                    return true;
                }}
                """
            )
            if opened:
                await self._page.locator(ASSET_MODAL_SELECTOR).first.wait_for(
                    state="visible",
                    timeout=5000,
                )
                logging.info("Panel de activos abierto con fallback DOM pair-number-wrap/current-symbol")
                return
        except Exception as exc:
            last_error = exc
            logging.info("Fallback DOM para abrir panel fallo: %s", exc)

        raise RuntimeError(f"No se pudo abrir el panel de activos: {last_error}")

    async def _resolve_asset_search_locator(self):
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        candidates = [
            self._asset_search_selector,
            f"{ASSET_MODAL_SELECTOR} input[type='text']",
            f"{ASSET_MODAL_SELECTOR} input",
            f"{ASSET_MODAL_SELECTOR} [role='searchbox']",
            f"{ASSET_MODAL_SELECTOR} [role='textbox']",
            f"{ASSET_MODAL_SELECTOR} [contenteditable='true']",
        ]

        for selector in candidates:
            if not selector:
                continue
            locator = self._page.locator(selector).first
            try:
                await locator.wait_for(state="visible", timeout=2500)
                logging.info("Buscador de activos resuelto con selector: %s", selector)
                return locator
            except Exception:
                continue

        raise RuntimeError("No se encontro el input de busqueda dentro del modal de activos")

    async def _resolve_asset_results_locator(self):
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        candidates = [
            self._asset_result_selector,
            f"{ASSET_MODAL_SELECTOR} [role='option']",
            f"{ASSET_MODAL_SELECTOR} .alist__item",
            f"{ASSET_MODAL_SELECTOR} .assets-block__alist li",
            f"{ASSET_MODAL_SELECTOR} li",
            f"{ASSET_MODAL_SELECTOR} a",
        ]

        for selector in candidates:
            if not selector:
                continue
            locator = self._page.locator(selector)
            try:
                await locator.first.wait_for(state="visible", timeout=2500)
                logging.info("Resultados de activos resueltos con selector: %s", selector)
                return locator
            except Exception:
                continue

        raise RuntimeError("No se encontraron resultados visibles dentro del modal de activos")

    async def _get_current_asset(self) -> str:
        if self._page is None:
            return ""

        selectors = [
            self._asset_open_selector,
            'span.current-symbol.current-symbol_cropped',
            'a.pair-number-wrap',
            '[class*="asset"]',
            '[data-testid*="asset"]',
        ]
        for selector in selectors:
            if not selector:
                continue
            try:
                locator = self._page.locator(selector).first
                if await locator.count() == 0:
                    continue
                text = (await locator.inner_text()).strip()
                if not text:
                    continue
                normalized = canonicalize_pocket_asset(text, default_asset="")
                if normalized:
                    return normalized
            except Exception:
                continue

        return ""

    async def _select_asset_from_results(self, target: str) -> bool:
        if self._page is None:
            return False

        results = await self._resolve_asset_results_locator()
        count = await results.count()
        if count == 0:
            logging.info("No hay resultados para asset %s", target)
            return False

        target_key = _asset_symbol_key(target)
        require_otc = "OTC" in target.upper()
        winner_idx = -1
        winner_score = -10_000
        valid_candidates = 0

        for idx in range(min(count, 40)):
            try:
                raw = (await results.nth(idx).inner_text()).strip()
            except Exception:
                continue
            if not raw:
                continue

            # CRITICAL FIX: Filter out unavailable assets immediately
            if "N/A" in raw:
                logging.info("Resultado idx=%s descartado (N/A, no disponible): %s", idx, raw[:120])
                continue
            
            if "%" not in raw:
                logging.info("Resultado idx=%s descartado (sin %%, no operable): %s", idx, raw[:120])
                continue

            valid_candidates += 1
            score = _score_asset_result(raw, target_key, require_otc)
            logging.info("Resultado activo válido idx=%s score=%s texto=%s", idx, score, raw[:120])
            if score > winner_score:
                winner_score = score
                winner_idx = idx

        if valid_candidates == 0:
            logging.warning(
                "Ningún candidato válido disponible para %s (todos N/A o sin payout)",
                target,
            )
            raise RuntimeError(f"ACTIVO_NO_DISPONIBLE: {target} no tiene opciones operables")

        if winner_idx < 0 or winner_score < 1:
            logging.warning(
                "No se encontro candidato con score positivo para %s (best_score=%s)",
                target,
                winner_score,
            )
            return False

        await self._click_asset_result(results.nth(winner_idx))
        return True

    async def _click_asset_result(self, locator) -> None:
        try:
            await locator.click(timeout=2500)
            logging.info("Click en contenedor de resultado OK")
            return
        except Exception as exc:
            logging.info("Click en contenedor fallo, intentando hijo: %s", exc)

        try:
            child = locator.locator("a, button, div").first
            if await child.count() > 0:
                await child.click(timeout=2500, force=True)
                logging.info("Click en hijo del resultado OK")
                return
        except Exception as exc:
            logging.info("Click en hijo del resultado fallo: %s", exc)

        logging.info("Click forzado sobre contenedor de resultado como ultimo recurso")
        await locator.click(timeout=2500, force=True)

    async def _verify_asset_changed(self, target: str) -> None:
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        current_asset = await self._wait_asset_changed(target, timeout_ms=5000)
        if current_asset:
            logging.info("Activo confirmado tras cambio: %s", current_asset)
            return

        raise RuntimeError(
            f"No cambio el activo tras click. esperado={target}"
        )

    async def _wait_asset_changed(self, target: str, timeout_ms: int = 5000) -> str:
        deadline = time.monotonic() + (timeout_ms / 1000)
        last_seen = ""

        while time.monotonic() < deadline:
            current_asset = await self._get_current_asset()
            if current_asset:
                last_seen = current_asset
                logging.info("Activo actual detectado durante verificacion: %s", current_asset)
            if _asset_selection_matches(current_asset, target):
                return current_asset
            await asyncio.sleep(0.3)

        if last_seen:
            logging.info("Activo final observado sin match: %s", last_seen)
        return ""

    async def _read_balance_from_page(self) -> float:
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        if self._balance_selector:
            explicit = self._page.locator(self._balance_selector).first
            if await explicit.count() > 0:
                raw = (await explicit.inner_text()).strip()
                values = _extract_numbers(raw)
                if values:
                    return max(values)

        selectors = [
            '[data-testid="balance"]',
            '.balance',
            '.js-balance',
            '.cabinet-profile-balance',
            '[class*="balance"]',
            '[id*="balance"]',
        ]

        candidates: list[tuple[int, float, str, str]] = []

        for selector in selectors:
            locator = self._page.locator(selector)
            count = await locator.count()
            if count == 0:
                continue

            for idx in range(min(count, 8)):
                raw_text = (await locator.nth(idx).inner_text()).strip()
                for value in _extract_numbers(raw_text):
                    score = _score_balance_candidate(raw_text, selector, value)
                    if score > 0:
                        candidates.append((score, value, raw_text, selector))

        balance_like_texts = await self._page.evaluate(
            """
            () => {
                const nodes = Array.from(document.querySelectorAll('[class*="balance"], [id*="balance"]'));
                return nodes
                  .map((n) => (n.textContent || '').trim())
                  .filter((t) => t.length > 0)
                  .slice(0, 20);
            }
            """
        )

        for item in balance_like_texts:
            for value in _extract_numbers(item):
                score = _score_balance_candidate(item, "dom-balance-like", value)
                if score > 0:
                    candidates.append((score, value, item, "dom-balance-like"))

        if candidates:
            chosen = max(candidates, key=lambda x: (x[0], x[1]))
            logging.info(
                "Saldo elegido=%s (score=%s, selector=%s, fuente=%s)",
                chosen[1],
                chosen[0],
                chosen[3],
                chosen[2][:120],
            )
            return chosen[1]

        raise RuntimeError(
            "No se pudo leer el saldo demo. Abre Pocket Option en modo demo "
            "y verifica que la sesion este iniciada en el navegador del perfil persistente."
        )

    async def _set_amount(self, amount: float, max_retries: int = 3) -> None:
        """Inyecta el monto en el campo con fallback chain robusto.
        Similar a _ensure_asset(), reintenta múltiples veces y múltiples selectores.
        """
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        amount_text = f"{amount:.2f}"
        attempts = max(1, min(max_retries, 5))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                await self._set_amount_once(amount_text)
                logging.info(
                    "✓ Monto inyectado correctamente: $%.2f (intento %d/%d)",
                    amount,
                    attempt,
                    attempts,
                )
                return
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "⚠ Intento %d/%d de inyectar monto $%.2f falló: %s",
                    attempt,
                    attempts,
                    amount,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(0.5)

        raise RuntimeError(f"No se pudo inyectar monto {amount_text}: {last_error}")

    async def _set_expiry_minutes(self, expiry_minutes: int, max_retries: int = 3) -> None:
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        target = max(1, int(expiry_minutes))
        attempts = max(1, min(max_retries, 5))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                await self._set_expiry_minutes_once(target)
                logging.info(
                    "✓ Expiracion configurada: %sm (intento %d/%d)",
                    target,
                    attempt,
                    attempts,
                )
                return
            except Exception as exc:
                last_error = exc
                logging.warning(
                    "⚠ Intento %d/%d de configurar expiracion %sm fallo: %s",
                    attempt,
                    attempts,
                    target,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(0.4)

        raise RuntimeError(f"No se pudo configurar expiracion {target}m: {last_error}")

    async def _set_expiry_minutes_once(self, expiry_minutes: int) -> None:
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        # Si ya está en el valor deseado, salir temprano.
        current = await self._read_expiry_label()
        if _expiry_label_matches(current, expiry_minutes):
            logging.info("Expiracion ya configurada en %sm (%s)", expiry_minutes, current)
            return

        openers = [
            "#put-call-buttons-chart-1 .block--expiration-inputs .value__val",
            "#put-call-buttons-chart-1 .block--expiration-inputs .control__value",
            "#put-call-buttons-chart-1 .block--expiration-inputs",
            ".block--expiration-inputs .value__val",
        ]

        opened = False
        for selector in openers:
            try:
                opener = self._page.locator(selector).first
                if await opener.count() == 0 or not await opener.is_visible():
                    continue
                await opener.click(timeout=1800)
                await asyncio.sleep(0.35)
                opened = True
                break
            except Exception:
                continue

        if not opened:
            raise RuntimeError("No se pudo abrir el panel de expiracion")

        option_selectors = [
            f".expiration-inputs-list-modal .dops__timeframes-item:has-text('M{expiry_minutes}')",
            f".trading-panel-modal .dops__timeframes-item:has-text('M{expiry_minutes}')",
            f".drop-down-modal .dops__timeframes-item:has-text('M{expiry_minutes}')",
            f"text=/^\\s*M\\s*{expiry_minutes}\\s*$/i",
        ]

        clicked = False
        for selector in option_selectors:
            try:
                option = self._page.locator(selector).first
                if await option.count() == 0 or not await option.is_visible():
                    continue
                await option.click(timeout=1800)
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            # cerrar modal abierto para no bloquear UI
            try:
                await self._page.keyboard.press("Escape")
            except Exception:
                pass
            raise RuntimeError(f"No se encontro opcion de expiracion M{expiry_minutes}")

        await asyncio.sleep(0.25)
        # Cerrar panel/modal de expiracion si quedó abierto
        try:
            await self._page.keyboard.press("Escape")
        except Exception:
            pass

        current_after = await self._read_expiry_label()
        if not _expiry_label_matches(current_after, expiry_minutes):
            raise RuntimeError(
                f"Expiracion no reflejada en UI. esperado={expiry_minutes}m actual='{current_after}'"
            )

    async def _read_expiry_label(self) -> str:
        if self._page is None:
            return ""

        candidates = [
            "#put-call-buttons-chart-1 .block--expiration-inputs .value__val",
            ".block--expiration-inputs .value__val",
            "#put-call-buttons-chart-1 .block--expiration-inputs .control__value",
        ]
        for selector in candidates:
            try:
                loc = self._page.locator(selector).first
                if await loc.count() == 0 or not await loc.is_visible():
                    continue
                txt = (await loc.inner_text(timeout=1200)).strip()
                if txt:
                    return txt
            except Exception:
                continue
        return ""

    async def _set_amount_once(self, amount_text: str) -> None:
        """Intenta una sola vez con fallback chain."""
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        selectors = [
            s
            for s in [
                self._amount_selector,
                # Selectores conocidos de Pocket Option
                'input[type="text"]:visible',  # Cualquier input visible tipo text
                '.block--bet-amount input',  # class actual del monto
                'input[name="amount"]',
                'input[data-testid="amount"]',
                'input[class*="amount"]',
                'input[type="number"]',  # para type=number inputs
                '#amount',
                'input[type="tel"]',
            ]
            if s
        ]

        last_exc: Exception | None = None
        for selector in selectors:
            try:
                locator = self._page.locator(selector).first
                if await locator.count() == 0:
                    continue

                # Verificar que sea visible ANTES de intentar clickear
                try:
                    await locator.is_visible(timeout=500)
                except:
                    continue

                # Click + clear + type
                await locator.click(timeout=1500)
                await locator.fill("", timeout=1500)
                await locator.type(amount_text, timeout=1500)
                logging.debug("Monto inyectado con selector: %s", selector)
                return
            except Exception as exc:
                last_exc = exc
                continue

        raise RuntimeError(f"No se encontro campo de monto. Ultima excepcion: {last_exc}")

    async def _click_side(self, side: str, max_retries: int = 3) -> None:
        """Clickea el botón BUY o SELL con reintentos robustos."""
        if self._page is None:
            raise RuntimeError("Cliente no conectado")

        normalized = side.upper()
        if normalized not in ("BUY", "CALL", "SELL", "PUT"):
            raise RuntimeError(f"side invalido: {side}")

        selector = self._buy_selector if normalized in ("BUY", "CALL") else self._sell_selector
        if not selector:
            raise RuntimeError(f"Selector {side} no configurado")

        attempts = max(1, min(max_retries, 5))
        for attempt in range(1, attempts + 1):
            try:
                locator = self._page.locator(selector).first
                if await locator.count() == 0:
                    raise RuntimeError(f"Selector no encontrado: {selector}")
                await locator.click(timeout=2000)
                logging.info("Click %s ejecutado (intento %d/%d)", side, attempt, attempts)
                return
            except Exception as exc:
                logging.warning(
                    "_click_side %s intento %d/%d fallo: %s",
                    side,
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(0.3)

        raise RuntimeError(f"No se pudo clickear {side} después de {attempts} intentos")

        text_patterns: Iterable[str]
        if normalized == "BUY":
            text_patterns = ("buy", "up", "call", "higher")
        else:
            text_patterns = ("sell", "down", "put", "lower")

        for name in text_patterns:
            button = self._page.get_by_role("button", name=re.compile(name, re.IGNORECASE)).first
            if await button.count() == 0:
                continue
            try:
                await button.click(timeout=2000)
                return
            except Exception:
                continue

        # Fallback heuristic: attempt to click large green/red actionable blocks.
        if await self._click_side_by_visual_heuristic(normalized):
            return

        raise RuntimeError(f"No se encontro boton para lado {normalized}")

    async def _click_side_by_visual_heuristic(self, side: str) -> bool:
        if self._page is None:
            return False

        result = await self._page.evaluate(
            """
            ({ side }) => {
                const isBuy = side === 'BUY';
                const elems = Array.from(document.querySelectorAll('button, div, a, span'));
                const candidates = [];

                for (const el of elems) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 90 || rect.height < 28) continue;
                    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;

                    const style = window.getComputedStyle(el);
                    const cursor = style.cursor || '';
                    const clickable = cursor === 'pointer' || el.tagName === 'BUTTON' || !!el.onclick;
                    if (!clickable) continue;

                    const colorBlob = [style.backgroundColor, style.color, style.borderColor]
                        .join(' ')
                        .toLowerCase();
                    const text = (el.textContent || '').trim().toLowerCase();

                    let score = 0;
                    if (isBuy) {
                        if (colorBlob.includes('0, 128') || colorBlob.includes('0,128') || colorBlob.includes('green')) score += 6;
                        if (text.includes('buy') || text.includes('up') || text.includes('call') || text.includes('higher')) score += 8;
                    } else {
                        if (colorBlob.includes('255, 0') || colorBlob.includes('255,0') || colorBlob.includes('red')) score += 6;
                        if (text.includes('sell') || text.includes('down') || text.includes('put') || text.includes('lower')) score += 8;
                    }

                    if (rect.width > 140) score += 1;
                    if (rect.height > 36) score += 1;
                    if (rect.top > window.innerHeight * 0.35) score += 1;

                    if (score > 0) {
                        candidates.push({ el, score, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
                    }
                }

                if (candidates.length === 0) return false;
                candidates.sort((a, b) => b.score - a.score);
                const winner = candidates[0];
                winner.el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                return true;
            }
            """,
            {"side": side},
        )

        return bool(result)


def _extract_numbers(text: str) -> list[float]:
    cleaned = text.replace("\u00a0", " ")
    matches = re.findall(r"(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?)", cleaned)
    values: list[float] = []
    for token in matches:
        parsed = _parse_number_token(token)
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_number_token(token: str) -> float | None:
    value = token.replace(" ", "")
    if value.count(",") > 0 and value.count(".") > 0:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif value.count(",") > 0:
        parts = value.split(",")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            value = "".join(parts)
        else:
            value = value.replace(",", ".")
    elif value.count(".") > 0:
        parts = value.split(".")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            value = "".join(parts)

    try:
        return float(value)
    except ValueError:
        return None


def _score_balance_candidate(raw_text: str, selector: str, value: float) -> int:
    score = 0
    text = raw_text.lower()
    sel = selector.lower()
    compact = " ".join(text.split())

    if "data-testid=\"balance\"" in sel:
        score += 100
    if "cabinet-profile-balance" in sel:
        score += 90
    if "balance" in sel:
        score += 70
    if "$" in raw_text or "usd" in text or "usdt" in text:
        score += 30
    if "balance" in text or "saldo" in text or "demo" in text:
        score += 20
    if "%" in raw_text:
        score -= 40
    if value >= 100:
        score += 15

    # Penaliza textos de modales de deposito/recarga que suelen traer +$1000/+${...}
    deposit_markers = (
        "add money",
        "enter the amount",
        "deposit",
        "top up",
        "recharge",
        "recarga",
        "agregar dinero",
    )
    if any(marker in compact for marker in deposit_markers):
        score -= 250

    if "+$" in raw_text or "+$" in compact:
        score -= 160

    number_count = len(_extract_numbers(raw_text))
    if number_count >= 2:
        score -= 70

    if ("balance" in compact or "saldo" in compact) and number_count == 1:
        score += 40

    return score


def _search_query_for_asset(asset: str) -> str:
    text = (asset or "").upper().replace("OTC", "").strip()
    letters = re.sub(r"[^A-Z]", "", text)
    if len(letters) >= 6:
        return letters[:6]
    return text


def _asset_symbol_key(text: str) -> str:
    return re.sub(r"[^A-Z]", "", (text or "").upper().replace("OTC", ""))


def _normalize_asset_text(text: str) -> str:
    cleaned = (text or "").upper().replace("(", " ").replace(")", " ")
    return re.sub(r"[^A-Z0-9]", "", cleaned)


def _asset_selection_matches(current_asset: str, target_asset: str) -> bool:
    current_key = _asset_symbol_key(current_asset)
    target_key = _asset_symbol_key(target_asset)
    if not current_key or not target_key or current_key != target_key:
        return False

    current_normalized = _normalize_asset_text(current_asset)
    target_normalized = _normalize_asset_text(target_asset)
    current_otc = "OTC" in current_normalized
    target_otc = "OTC" in target_normalized
    return current_otc == target_otc


def _expiry_label_matches(label: str, expiry_minutes: int) -> bool:
    text = (label or "").strip().upper()
    if not text:
        return False

    # Examples expected from UI: M5, 00:05:00, 00:05
    if f"M{expiry_minutes}" in text or f"M {expiry_minutes}" in text:
        return True
    if f":{expiry_minutes:02d}:" in text:
        return True
    if text.endswith(f":{expiry_minutes:02d}"):
        return True
    return False


def _score_asset_result(raw_text: str, target_key: str, require_otc: bool) -> int:
    """
    Score an asset result based on match quality and tradability.
    
    CRITICAL FIX: Penalize unavailable assets (N/A) heavily to prevent
    selecting assets that can't be traded.
    
    Scoring:
    - Perfect match (target_key == key): +120
    - Partial match: +40-80
    - Available (has "%"): +50 bonus
    - Unavailable (has "N/A"): -300 penalty (disqualify)
    - OTC preference: +30 if required, +5 if optional
    """
    normalized = (raw_text or "").upper()
    key = _asset_symbol_key(normalized)
    has_otc = "OTC" in normalized
    
    # CRITICAL FIX: Check if asset is available
    # If "N/A" is present, this asset is NOT tradable
    is_available = "N/A" not in normalized and "%" in normalized
    
    score = 0
    
    # Symbol matching
    if target_key == key:
        score += 120
    elif target_key and target_key in key:
        score += 80
    elif key and key in target_key:
        score += 40
    
    # Availability (most important after symbol match)
    if is_available:
        score += 50  # Bonus for tradable assets
    else:
        # CRITICAL FIX: Heavily penalize unavailable assets
        # This makes them lose to available alternatives
        score -= 300
    
    # OTC preference
    if require_otc:
        score += 30 if has_otc else -120
    elif has_otc:
        score += 5
    
    return score


def _is_target_closed_error(exc: BaseException) -> bool:
    lowered = str(exc).lower()
    return (
        "target page, context or browser has been closed" in lowered
        or "target closed" in lowered
        or "connection closed" in lowered
    )


def _is_profile_in_use_error(exc: BaseException) -> bool:
    lowered = str(exc).lower()
    return (
        "user data directory is already in use" in lowered
        or "process singleton" in lowered
        or "profile appears to be in use" in lowered
        or "another browser" in lowered and "profile" in lowered
    )


def _is_transient_navigation_error(exc: BaseException) -> bool:
    lowered = str(exc).lower()
    transient_markers = (
        "net::err_connection_timed_out",
        "net::err_timed_out",
        "net::err_aborted",
        "net::err_connection_reset",
        "net::err_connection_closed",
        "net::err_network_changed",
        "net::err_internet_disconnected",
        "navigation timeout",
        "timeout",
    )
    return any(marker in lowered for marker in transient_markers)
