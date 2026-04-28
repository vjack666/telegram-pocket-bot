from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any


_ASSET_TOKEN_RE = re.compile(r"[A-Z]{3}\s*/\s*[A-Z]{3}(?:\s+OTC)?|[A-Z]{6}(?:\s+OTC)?", re.IGNORECASE)


def _asset_key(raw: str) -> str:
    txt = (raw or "").upper().replace("OTC", "")
    return re.sub(r"[^A-Z]", "", txt)


def _extract_currency_numbers(text: str) -> list[float]:
    matches = re.findall(r"\$\s*\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d+)?", text)
    values: list[float] = []
    for token in matches:
        parsed = _parse_number(token)
        if parsed is not None:
            values.append(parsed)
    return values


def _parse_number(token: str) -> float | None:
    value = token.replace("$", "").replace(" ", "")
    value = value.replace("+", "")
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


def _parse_price(token: str) -> float | None:
    value = (token or "").strip().replace(" ", "")
    value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class LiveTradeSnapshot:
    asset: str
    pnl_value: float
    raw_text: str
    confidence: int
    captured_ts: float
    forecast_side: str | None = None
    open_price: float | None = None
    close_price: float | None = None
    open_price_decimals: int | None = None
    close_price_decimals: int | None = None

    @property
    def status(self) -> str:
        if self.forecast_side and self.open_price is not None and self.close_price is not None:
            decimals = max(self.open_price_decimals or 0, self.close_price_decimals or 0)
            epsilon = 10 ** (-decimals) if decimals > 0 else 1e-6
            diff = self.close_price - self.open_price

            side = self.forecast_side.upper()
            if side in {"SELL", "PUT", "DOWN"}:
                if diff < -epsilon:
                    return "WINNING"
                if diff > epsilon:
                    return "LOSING"
                return "NEUTRAL"
            if side in {"BUY", "CALL", "UP"}:
                if diff > epsilon:
                    return "WINNING"
                if diff < -epsilon:
                    return "LOSING"
                return "NEUTRAL"

        if self.pnl_value > 0.0001:
            return "WINNING"
        if self.pnl_value < -0.0001:
            return "LOSING"
        return "NEUTRAL"


class TradePanelFeed:
    """Lee estado vivo de operaciones abiertas desde el panel de Trades del broker."""

    async def read_live_snapshot(
        self,
        page: Any,
        target_asset: str,
        target_side: str | None = None,
    ) -> LiveTradeSnapshot | None:
        if page is None:
            return None

        rows = await page.evaluate(
            r"""
            () => {
                const selectors = [
                    '[class*="trades"] [class*="item"]',
                    '[class*="deals"] [class*="item"]',
                    '[class*="opened"] [class*="item"]',
                    '[class*="trades"] [class*="deal"]',
                    '[class*="deals-list"] [class*="deal"]',
                ];

                const visible = (el) => {
                    const st = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return st.visibility !== 'hidden' && st.display !== 'none' && r.width > 8 && r.height > 8;
                };

                const out = [];
                const seen = new Set();

                for (const sel of selectors) {
                    const nodes = Array.from(document.querySelectorAll(sel));
                    for (const node of nodes) {
                        if (!visible(node)) continue;
                        const txt = (node.textContent || '').replace(/\s+/g, ' ').trim();
                        if (!txt || txt.length < 8 || txt.length > 240) continue;
                        if (!txt.includes('$')) continue;
                        if (!(txt.includes('OTC') || /[A-Z]{3}\/[A-Z]{3}/.test(txt) || /[A-Z]{6}/.test(txt))) continue;
                        if (seen.has(txt)) continue;
                        seen.add(txt);
                        out.push(txt);
                    }
                }

                if (out.length > 0) {
                    return out.slice(0, 40);
                }

                const fallbackNodes = Array.from(document.querySelectorAll('div,li,section,article'));
                for (const node of fallbackNodes) {
                    if (!visible(node)) continue;
                    const txt = (node.textContent || '').replace(/\s+/g, ' ').trim();
                    if (!txt || txt.length < 12 || txt.length > 320) continue;
                    if (!txt.includes('$')) continue;
                    if (!(txt.includes('OTC') || /[A-Z]{3}\/[A-Z]{3}/.test(txt) || /[A-Z]{6}/.test(txt))) continue;
                    if (seen.has(txt)) continue;
                    seen.add(txt);
                    out.push(txt);
                    if (out.length >= 40) break;
                }

                return out;
            }
            """
        )

        if not rows:
            return None

        target_key = _asset_key(target_asset)
        normalized_target_side = self._normalize_side(target_side)
        winner: LiveTradeSnapshot | None = None

        for raw in rows:
            snapshot = self._parse_row(raw, target_key, normalized_target_side)
            if snapshot is None:
                continue
            if winner is None or snapshot.confidence > winner.confidence:
                winner = snapshot

        return winner

    def _parse_row(
        self,
        raw_text: str,
        target_key: str,
        target_side: str | None,
    ) -> LiveTradeSnapshot | None:
        text = (raw_text or "").strip()
        if not text:
            return None

        asset_match = _ASSET_TOKEN_RE.search(text)
        if not asset_match:
            return None

        asset_text = asset_match.group(0).upper().replace("  ", " ").strip()
        asset_key = _asset_key(asset_text)

        confidence = 0
        if target_key and asset_key == target_key:
            confidence += 120
        elif target_key and target_key in asset_key:
            confidence += 80
        elif target_key and asset_key in target_key:
            confidence += 60
        else:
            confidence += 10

        if "OTC" in text.upper():
            confidence += 20

        pnl_value = self._extract_live_pnl(text)
        if pnl_value is None:
            return None

        forecast_side = self._extract_forecast_side(text)
        normalized_forecast_side = self._normalize_side(forecast_side)

        if target_side and normalized_forecast_side is not None:
            if target_side == normalized_forecast_side:
                confidence += 80
            else:
                # Si hay side explícito y no coincide, no usar esta fila.
                return None

        open_price, open_price_decimals = self._extract_named_price(text, r"OPEN\s*PRICE")
        close_price, close_price_decimals = self._extract_named_price(text, r"CLOSING\s*PRICE")

        if pnl_value > 0:
            confidence += 15
        elif pnl_value < 0:
            confidence += 15

        return LiveTradeSnapshot(
            asset=asset_text,
            pnl_value=pnl_value,
            raw_text=text,
            confidence=confidence,
            captured_ts=time.time(),
            forecast_side=forecast_side,
            open_price=open_price,
            close_price=close_price,
            open_price_decimals=open_price_decimals,
            close_price_decimals=close_price_decimals,
        )

    @staticmethod
    def _normalize_side(side: str | None) -> str | None:
        if side is None:
            return None
        token = side.upper().strip()
        if token in {"BUY", "CALL", "UP"}:
            return "BUY"
        if token in {"SELL", "PUT", "DOWN"}:
            return "SELL"
        return None

    @staticmethod
    def _extract_forecast_side(text: str) -> str | None:
        match = re.search(r"YOUR\s+FORECAST\s*:\s*(BUY|SELL|CALL|PUT|UP|DOWN)", text, re.IGNORECASE)
        if not match:
            return None
        token = match.group(1).upper()
        if token in {"SELL", "PUT", "DOWN"}:
            return "SELL"
        return "BUY"

    @staticmethod
    def _extract_named_price(text: str, label_pattern: str) -> tuple[float | None, int | None]:
        match = re.search(rf"{label_pattern}\s*:\s*(\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
        if not match:
            return None, None
        token = match.group(1)
        parsed = _parse_price(token)
        if parsed is None:
            return None, None
        decimals = 0
        if "." in token:
            decimals = len(token.split(".")[-1])
        elif "," in token:
            decimals = len(token.split(",")[-1])
        return parsed, decimals

    def _extract_live_pnl(self, text: str) -> float | None:
        compact = " ".join(text.split())

        explicit_profit = re.search(r"PROFIT\s*:\s*\$\s*([+\-]?\d+(?:[.,]\d+)?)", compact, re.IGNORECASE)
        if explicit_profit:
            parsed = _parse_number(explicit_profit.group(1))
            if parsed is not None:
                return parsed

        explicit_payout = re.search(r"PAYOUT\s*:\s*\$\s*([+\-]?\d+(?:[.,]\d+)?)", compact, re.IGNORECASE)
        if explicit_payout:
            parsed = _parse_number(explicit_payout.group(1))
            if parsed is not None and parsed > 0:
                return parsed

        # Prioriza tokens tipo +$3.13 / -$1.20 que suelen ser P/L vivo en tarjeta abierta.
        signed = re.findall(r"([+\-]\s*\$\s*\d+(?:[.,]\d+)?)", compact)
        if signed:
            parsed = _parse_number(signed[-1])
            if parsed is not None:
                return parsed

        # Ignora diferencias en puntos; no representan P/L monetario liquidado.
        if re.search(r"POINTS?", compact, re.IGNORECASE):
            return None

        # Algunas variantes muestran el signo sin $: +3.13 / -1.20
        signed_plain = re.findall(r"([+\-]\s*\d+(?:[.,]\d+)?)", compact)
        if signed_plain:
            parsed = _parse_number(signed_plain[-1])
            if parsed is not None:
                return parsed

        currency_values = _extract_currency_numbers(compact)
        if explicit_profit or explicit_payout:
            return None

        if len(currency_values) >= 2:
            # En tarjetas abiertas suele verse: monto_invertido y retorno_actual.
            # Usamos la diferencia para inferir P/L cuando no viene explícito con signo.
            return currency_values[-1] - currency_values[0]

        if len(currency_values) == 1:
            return currency_values[0]

        if not currency_values:
            return None

        return None
