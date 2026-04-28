"""
candle_feed.py
==============
Captura datos de precio/vela de Pocket Option via WebSocket.

Uso dentro del cliente existente:

    from src.pocket_option.candle_feed import CandleFeed

    feed = CandleFeed()
    feed.attach(page)           # llamar después de page.goto(...)

    # obtener último tick
    tick = feed.last_tick("EURUSD")
    # tick.asset, tick.price, tick.ts (epoch float)

    # obtener vela aggregada actual
    candle = feed.current_candle("EURUSD")
    # candle.open, candle.high, candle.low, candle.close, candle.ts

    # esperar el próximo tick de un activo (con timeout)
    tick = await feed.wait_tick("EURUSD", timeout=10.0)

Pocket Option usa un protocolo WebSocket con mensajes JSON que
generalmente tienen esta forma (puede variar según versión de la plataforma):

    {"asset":"EURUSD","time":1714223412.5,"price":1.07234}
    {"asset":"EURUSD_OTC","time":...,"price":...}

    o como array de ticks:
    [{"asset":"EURUSD","time":...,"price":...}, ...]

    o mensajes de tipo "loadHistoryPeriod" / "updateHistoryNew" con arrays OHLC.

El módulo intentará detectar automáticamente el formato.
Ejecuta scripts/inspect_candle_ws.py para ver el formato real de tu cuenta.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

# Palabras clave que suelen indicar un mensaje de precio/tick en el WS de Pocket Option
_PRICE_KEYWORDS = re.compile(
    r'"price"|"tick"|"quote"|"ohlc"|"candle|loadHistory|updateHistory|stream',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Tick:
    asset: str
    price: float
    ts: float  # epoch seconds (float)

    @property
    def iso(self) -> str:
        import datetime
        return datetime.datetime.utcfromtimestamp(self.ts).isoformat() + "Z"


@dataclass
class Candle:
    asset: str
    open: float
    high: float
    low: float
    close: float
    ts: float  # timestamp de apertura de la vela

    @property
    def direction(self) -> str:
        if self.close > self.open:
            return "CALL"
        if self.close < self.open:
            return "PUT"
        return "NEUTRAL"


# ---------------------------------------------------------------------------
# Parser helpers — formato Pocket Option
# ---------------------------------------------------------------------------

def _normalize_asset(raw: str) -> str:
    """Normaliza el nombre de activo: 'EURUSD_OTC' → 'EURUSD OTC'."""
    return raw.strip().replace("_OTC", " OTC").replace("_otc", " OTC").upper()


def _parse_tick_from_dict(d: dict) -> Tick | None:
    """Intenta extraer un Tick de un objeto JSON de PO."""
    # Formato common: {"asset":"EURUSD","time":..., "price":...}
    # Formato alternativo: {"symbol":"EURUSD", "t":..., "p":...}
    asset_raw = (
        d.get("asset") or d.get("symbol") or d.get("pair") or d.get("name") or ""
    )
    if not asset_raw:
        return None

    price = (
        d.get("price") or d.get("p") or d.get("close") or d.get("c")
    )
    if price is None:
        return None

    ts = float(
        d.get("time") or d.get("t") or d.get("ts") or d.get("timestamp") or time.time()
    )

    try:
        return Tick(asset=_normalize_asset(str(asset_raw)), price=float(price), ts=ts)
    except (ValueError, TypeError):
        return None


def _parse_candle_from_dict(d: dict) -> Candle | None:
    """Intenta extraer una Candle de un objeto JSON de PO (formato OHLC)."""
    asset_raw = d.get("asset") or d.get("symbol") or d.get("pair") or d.get("name") or ""
    if not asset_raw:
        return None

    o = d.get("open") or d.get("o")
    h = d.get("high") or d.get("h")
    l = d.get("low") or d.get("l")
    c = d.get("close") or d.get("c")
    if None in (o, h, l, c):
        return None

    ts = float(d.get("time") or d.get("t") or d.get("ts") or d.get("timestamp") or time.time())

    try:
        return Candle(
            asset=_normalize_asset(str(asset_raw)),
            open=float(o), high=float(h), low=float(l), close=float(c),
            ts=ts,
        )
    except (ValueError, TypeError):
        return None


def _parse_payload(raw: str | bytes) -> list[Tick | Candle]:
    """Parsea un frame WebSocket y devuelve lista de Tick/Candle extraídos."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return []
    else:
        text = raw

    if not _PRICE_KEYWORDS.search(text):
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    results: list[Tick | Candle] = []
    items: list[Any] = data if isinstance(data, list) else [data]

    for item in items:
        if not isinstance(item, dict):
            continue

        # Buscar en sub-keys comunes de PO: "data", "candles", "ticks", "history"
        for sub_key in ("data", "candles", "ticks", "history", "quotes"):
            sub = item.get(sub_key)
            if isinstance(sub, list):
                for sub_item in sub:
                    if isinstance(sub_item, dict):
                        c = _parse_candle_from_dict(sub_item)
                        if c:
                            results.append(c)
                        else:
                            t = _parse_tick_from_dict(sub_item)
                            if t:
                                results.append(t)
                break

        # Intentar parsear el propio item
        c = _parse_candle_from_dict(item)
        if c:
            results.append(c)
        else:
            t = _parse_tick_from_dict(item)
            if t:
                results.append(t)

    return results


# ---------------------------------------------------------------------------
# CandleFeed — clase principal
# ---------------------------------------------------------------------------

class CandleFeed:
    """
    Se engancha a un objeto `page` de Playwright y escucha frames WebSocket
    para capturar precios y velas de Pocket Option en tiempo real.

    Uso:
        feed = CandleFeed()
        feed.attach(page)
        ...
        tick = feed.last_tick("EURUSD")
        candle = feed.current_candle("EURUSD")
        tick = await feed.wait_tick("EURUSD", timeout=10.0)
    """

    def __init__(self) -> None:
        # asset -> Tick más reciente
        self._ticks: dict[str, Tick] = {}
        # asset -> Candle actual (en construcción)
        self._candles: dict[str, Candle] = {}
        # asset -> lista de listeners (futures) esperando el próximo tick
        self._waiters: dict[str, list[asyncio.Future[Tick]]] = {}
        self._total_frames = 0
        self._parsed_frames = 0
        self._on_tick_callbacks: list[Callable[[Tick], None]] = []
        self._on_candle_callbacks: list[Callable[[Candle], None]] = []

    # ------------------------------------------------------------------
    # Attach
    # ------------------------------------------------------------------

    def attach(self, page) -> None:
        """
        Engancha el feed al objeto page de Playwright.
        Llamar después de page.goto().
        """
        page.on("websocket", self._on_websocket)
        log.info("[CandleFeed] Enganchado al WebSocket de la página")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def last_tick(self, asset: str) -> Tick | None:
        """Devuelve el último tick recibido para el activo, o None."""
        return self._ticks.get(_normalize_asset(asset))

    def current_candle(self, asset: str) -> Candle | None:
        """Devuelve la vela actual (en construcción) para el activo, o None."""
        return self._candles.get(_normalize_asset(asset))

    async def wait_tick(self, asset: str, timeout: float = 10.0) -> Tick | None:
        """
        Espera el próximo tick para el activo.
        Retorna None si supera el timeout.
        """
        key = _normalize_asset(asset)
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[Tick] = loop.create_future()

        waiters = self._waiters.setdefault(key, [])
        waiters.append(fut)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            try:
                waiters.remove(fut)
            except ValueError:
                pass
            return None

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        """Registra un callback que se llama con cada Tick recibido."""
        self._on_tick_callbacks.append(callback)

    def on_candle(self, callback: Callable[[Candle], None]) -> None:
        """Registra un callback que se llama con cada Candle recibida."""
        self._on_candle_callbacks.append(callback)

    @property
    def stats(self) -> dict:
        return {
            "total_frames": self._total_frames,
            "parsed_frames": self._parsed_frames,
            "assets_with_ticks": list(self._ticks.keys()),
            "assets_with_candles": list(self._candles.keys()),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_websocket(self, ws) -> None:
        log.debug("[CandleFeed] WS conectado: %s", ws.url)
        ws.on("framereceived", self._on_frame)

    def _on_frame(self, payload: str | bytes) -> None:
        self._total_frames += 1
        items = _parse_payload(payload)
        if not items:
            return

        self._parsed_frames += 1
        for item in items:
            if isinstance(item, Tick):
                self._handle_tick(item)
            elif isinstance(item, Candle):
                self._handle_candle(item)

    def _handle_tick(self, tick: Tick) -> None:
        self._ticks[tick.asset] = tick

        # Actualizar vela en construcción
        existing = self._candles.get(tick.asset)
        if existing is None:
            self._candles[tick.asset] = Candle(
                asset=tick.asset,
                open=tick.price, high=tick.price,
                low=tick.price, close=tick.price,
                ts=tick.ts,
            )
        else:
            existing.close = tick.price
            existing.high = max(existing.high, tick.price)
            existing.low = min(existing.low, tick.price)

        log.debug("[CandleFeed] tick %s price=%.5f", tick.asset, tick.price)

        # Notificar waiters
        for fut in list(self._waiters.get(tick.asset, [])):
            if not fut.done():
                fut.set_result(tick)
        self._waiters.pop(tick.asset, None)

        # Callbacks
        for cb in self._on_tick_callbacks:
            try:
                cb(tick)
            except Exception as exc:
                log.warning("[CandleFeed] error en on_tick callback: %s", exc)

    def _handle_candle(self, candle: Candle) -> None:
        self._candles[candle.asset] = candle
        log.debug(
            "[CandleFeed] candle %s O=%.5f H=%.5f L=%.5f C=%.5f dir=%s",
            candle.asset, candle.open, candle.high, candle.low, candle.close,
            candle.direction,
        )

        # Callbacks
        for cb in self._on_candle_callbacks:
            try:
                cb(candle)
            except Exception as exc:
                log.warning("[CandleFeed] error en on_candle callback: %s", exc)
