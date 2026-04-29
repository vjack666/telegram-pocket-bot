import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from src.core.models import TradingSignal
from src.pocket_option.assets import canonicalize_pocket_asset


class SignalParser:
    def __init__(
        self,
        default_amount: float = 1.0,
        signal_tz_offset_hours: int = -3,
        signal_timezone: str = "America/Argentina/Buenos_Aires",
    ) -> None:
        self._default_amount = default_amount
        if ZoneInfo is not None:
            try:
                self._signal_tz = ZoneInfo(signal_timezone)
            except Exception:
                self._signal_tz = timezone(timedelta(hours=signal_tz_offset_hours))
        else:
            self._signal_tz = timezone(timedelta(hours=signal_tz_offset_hours))

        self._asset_pair_re = re.compile(
            r"\b([A-Z]{3})\s*/\s*([A-Z]{3})(\s+OTC)?\b",
            re.IGNORECASE,
        )
        self._asset_re = re.compile(
            r"\b([A-Z]{6}(?:\s+OTC)?|XAUUSD(?:\s+OTC)?|BTCUSDT(?:\s+OTC)?)\b",
            re.IGNORECASE,
        )
        self._asset_flex_re = re.compile(
            r"\b([A-Z]{3})\s*[-_/]?\s*([A-Z]{3})(?:\s*[-_/]?\s*(OTC))?\b",
            re.IGNORECASE,
        )
        self._side_re = re.compile(
            r"\b(BUY|SELL|CALL|PUT|UP|DOWN|ARRIBA|ABAJO|ALZA|BAJA)\b",
            re.IGNORECASE,
        )
        self._expiry_re = re.compile(
            r"(?:EXPIRACION\s*[:=]?\s*)?(\d{1,3})\s*(M|MIN|MINUTE|MINUTES|MINUTO|MINUTOS)\b",
            re.IGNORECASE,
        )
        self._expiry_compact_re = re.compile(r"\bM\s*(\d{1,3})\b|\b(\d{1,3})\s*M\b", re.IGNORECASE)
        self._amount_re = re.compile(
            r"\b(?:AMOUNT|MONTO|RISK|ENTRY|ENTRADA|VALOR|IMPORTE)\s*[:=]?\s*(\d+(?:[\.,]\d+)?)\b",
            re.IGNORECASE,
        )
        self._amount_inline_currency_re = re.compile(
            r"(?:\$\s*|USDT\s*)(\d+(?:[\.,]\d+)?)\b",
            re.IGNORECASE,
        )
        self._entry_time_re = re.compile(
            r"\b(?:ENTRADA\s*(?:A\s*LAS)?|ENTRY\s*(?:AT)?)\s*[:=]?\s*(\d{1,2})[:\.](\d{2})\b",
            re.IGNORECASE,
        )
        self._signal_line_re = re.compile(
            r"(?m)^[^A-Z0-9\r\n]*"
            r"(?P<asset>[A-Z]{3}\s*/\s*[A-Z]{3}(?:\s+OTC)?|[A-Z]{6}(?:\s+OTC)?)"
            r"\s*[-|]\s*"
            r"(?P<side>BUY|SELL|CALL|PUT|UP|DOWN|ARRIBA|ABAJO|ALZA|BAJA)\b"
            r".*?"
            r"[-|]\s*(?P<hour>\d{1,2})[:\.](?P<minute>\d{2})\b",
            re.IGNORECASE,
        )
        self._martingale_time_re = re.compile(
            r"MARTINGALA\s*(?:A\s*LAS)?\s*[:=]?\s*(\d{1,2})[:\.](\d{2})",
            re.IGNORECASE,
        )
        # Códigos de moneda y cripto reconocidos. Cualquier par que no use
        # exclusivamente estos códigos se descarta como falso positivo.
        self._known_currencies = {
            "USD", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "NZD",
            "SEK", "NOK", "DKK", "SGD", "HKD", "MXN", "ZAR", "TRY",
            "RUB", "BRL", "INR", "CNH", "CNY", "PLN", "HUF", "CZK",
            "XAU", "XAG", "XPT", "XPD",
            # Exóticos Pocket Option OTC
            "AED", "EGP", "SAR", "QAR", "KWD", "BHD", "OMR",
            "IDR", "MYR", "PHP", "THB", "VND", "PKR", "BDT",
            "KES", "NGN", "GHS", "UGX", "TZS",
            "CLP", "COP", "PEN", "ARS",
            "UAH", "KZT", "GEL",
            "BTC", "ETH", "LTC", "XRP", "BNB", "SOL", "ADA", "DOT",
            "USDT", "USDC",
        }

    def parse(self, raw_text: str, received_at_utc: datetime | None = None) -> Optional[TradingSignal]:
        text = (raw_text or "").strip()
        if not text:
            return None

        received = received_at_utc or TradingSignal.now_utc()
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        else:
            received = received.astimezone(timezone.utc)

        norm_text = _normalize_for_match(text)

        signal_line_match = self._signal_line_re.search(norm_text)
        side_match = self._side_re.search(norm_text)
        inferred_side = self._infer_side_from_symbols(text)
        if not side_match and not inferred_side:
            return None

        asset = self._extract_asset(norm_text, signal_line_match)
        if asset is None:
            return None
        expiry_match = self._expiry_re.search(norm_text)
        amount_match = self._amount_re.search(norm_text)
        entry_time_match = self._entry_time_re.search(norm_text)
        if entry_time_match is None:
            entry_time_match = signal_line_match
        martingale_time_matches = self._martingale_time_re.findall(norm_text)

        if signal_line_match is not None:
            side = self._normalize_side(signal_line_match.group("side"))
        elif side_match is not None:
            side = self._normalize_side(side_match.group(1))
        else:
            # inferred_side is guaranteed non-None: both side_match and inferred_side
            # being falsy was already rejected by the early return above.
            assert inferred_side is not None
            side = inferred_side
        expiry_minutes = int(expiry_match.group(1)) if expiry_match else 1
        if expiry_match is None:
            compact = self._expiry_compact_re.search(norm_text)
            if compact:
                expiry_minutes = int(compact.group(1) or compact.group(2))
        amount = (
            float(amount_match.group(1).replace(",", "."))
            if amount_match
            else self._default_amount
        )
        if amount_match is None:
            inline_amount = self._amount_inline_currency_re.search(norm_text)
            if inline_amount:
                amount = float(inline_amount.group(1).replace(",", "."))
        execute_at_utc = self._compute_execute_at_utc(entry_time_match, received)
        martingale_execute_at_utc = self._compute_martingale_times_utc(
            martingale_time_matches,
            execute_at_utc,
            received,
        )

        return TradingSignal(
            asset=asset,
            side=side,
            expiry_minutes=expiry_minutes,
            amount=amount,
            source_text=text,
            received_at=received,
            execute_at_utc=execute_at_utc,
            martingale_execute_at_utc=martingale_execute_at_utc,
        )

    @staticmethod
    def _normalize_side(raw_side: str) -> str:
        s = raw_side.upper()
        if s in {"CALL", "UP", "BUY", "ARRIBA", "ALZA"}:
            return "BUY"
        return "SELL"

    def _extract_asset(self, text: str, signal_line_match: re.Match[str] | None = None) -> str | None:
        if signal_line_match is not None:
            raw_asset = re.sub(r"\s+", " ", signal_line_match.group("asset").upper()).strip()
            return canonicalize_pocket_asset(raw_asset.replace("/", ""), default_asset="EURUSD OTC")

        pair_match = self._asset_pair_re.search(text)
        if pair_match:
            c1, c2 = pair_match.group(1).upper(), pair_match.group(2).upper()
            if c1 in self._known_currencies and c2 in self._known_currencies:
                raw_pair = f"{c1}{c2}"
                if pair_match.group(3):
                    raw_pair = f"{raw_pair} OTC"
                return canonicalize_pocket_asset(raw_pair, default_asset="EURUSD OTC")

        flex_match = self._asset_flex_re.search(text)
        if flex_match:
            c1, c2 = flex_match.group(1).upper(), flex_match.group(2).upper()
            if c1 in self._known_currencies and c2 in self._known_currencies:
                raw_pair = f"{c1}{c2}"
                if flex_match.group(3):
                    raw_pair = f"{raw_pair} OTC"
                return canonicalize_pocket_asset(raw_pair, default_asset="EURUSD OTC")

        asset_match = self._asset_re.search(text)
        if not asset_match:
            return None

        raw_asset = re.sub(r"\s+", " ", asset_match.group(1).upper()).strip()
        # Validar que el par de 6 letras esté compuesto por dos monedas conocidas
        base = raw_asset.replace(" OTC", "").replace(" ", "")
        if len(base) == 6:
            c1, c2 = base[:3], base[3:]
            if c1 not in self._known_currencies or c2 not in self._known_currencies:
                return None
        return canonicalize_pocket_asset(raw_asset, default_asset="EURUSD OTC")

    @staticmethod
    def _infer_side_from_symbols(raw_text: str) -> str | None:
        # Many channels publish direction using arrows/emojis instead of BUY/SELL words.
        up_markers = [
            "\u2b06",  # up arrow
            "\U0001f53c",  # up triangle button
            "\U0001f4c8",  # chart increasing
            "\U0001f7e2",  # green circle
        ]
        down_markers = [
            "\u2b07",  # down arrow
            "\U0001f53d",  # down triangle button
            "\U0001f4c9",  # chart decreasing
            "\U0001f534",  # red circle
        ]

        if any(marker in raw_text for marker in up_markers):
            return "BUY"
        if any(marker in raw_text for marker in down_markers):
            return "SELL"
        return None

    def _compute_execute_at_utc(
        self,
        match: re.Match[str] | None,
        received_at_utc: datetime,
    ) -> datetime | None:
        if match is None:
            return None

        groupdict = match.groupdict()
        if "hour" in groupdict and "minute" in groupdict:
            hour = int(groupdict["hour"])
            minute = int(groupdict["minute"])
        else:
            hour = int(match.group(1))
            minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return None

        base_local = received_at_utc.astimezone(self._signal_tz)
        scheduled_local = base_local.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If the message arrives close to midnight and entry is just after midnight,
        # schedule to the next day instead of the past day.
        if scheduled_local < base_local - timedelta(hours=12):
            scheduled_local += timedelta(days=1)

        return scheduled_local.astimezone(timezone.utc)

    def _compute_martingale_times_utc(
        self,
        matches: list[tuple[str, str]],
        entry_time_utc: datetime | None,
        received_at_utc: datetime,
    ) -> tuple[datetime, ...]:
        if not matches:
            return ()

        base_local = received_at_utc.astimezone(self._signal_tz)
        prev_local = entry_time_utc.astimezone(self._signal_tz) if entry_time_utc else None
        result: list[datetime] = []

        for hour_raw, minute_raw in matches:
            hour = int(hour_raw)
            minute = int(minute_raw)
            if hour > 23 or minute > 59:
                continue

            scheduled_local = base_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if scheduled_local < base_local - timedelta(hours=12):
                scheduled_local += timedelta(days=1)

            if prev_local is not None:
                while scheduled_local <= prev_local:
                    scheduled_local += timedelta(days=1)

            prev_local = scheduled_local
            result.append(scheduled_local.astimezone(timezone.utc))

        return tuple(result)


def _normalize_for_match(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return without_marks.upper()
