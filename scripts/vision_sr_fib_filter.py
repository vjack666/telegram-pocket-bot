from __future__ import annotations

import asyncio
import base64
import csv
import inspect
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import anthropic
from dotenv import load_dotenv
from playwright.async_api import BrowserContext, Page, async_playwright
from telethon import TelegramClient, events

from src.signals.parser import SignalParser


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "runtime"
SNAP_DIR = RUNTIME_DIR / "chart_snaps"
DEFAULT_DECISION_LOG = RUNTIME_DIR / "decisions.csv"

CLAUDE_PROMPT = """
Analyze this binary options chart image. Identify:
1. The 3 most visible support and resistance horizontal levels (price values)
2. The last clear swing high and swing low visible on the chart
3. Calculate the 61.8% Fibonacci retracement level between that swing high and low
4. Is the current price (last candle close) within 0.3% of the 61.8% level?
   Answer true or false.
5. Is the current price within 5 pips of any support or resistance zone?
   If yes, is it support (price bouncing up) or resistance (price bouncing down)?

Respond ONLY with this JSON, no extra text:
{
  "swing_high": 0.0,
  "swing_low": 0.0,
  "fib_618": 0.0,
  "near_fib618": false,
  "sr_zone_type": "none",
  "sr_zone_price": 0.0,
  "near_sr": false,
  "bias": "NEUTRAL",
  "approved": false,
  "reason": ""
}
""".strip()


@dataclass
class TradingSignalEvent:
    asset: str
    direction: str
    entry_time_utc: datetime | None
    source_chat: str
    raw_text: str
    message_id: int
    received_at_utc: datetime


@dataclass
class VisionDecision:
    approved: bool
    reason: str
    fib_level: float | None
    sr_zone: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")


def _normalize_direction(raw: str) -> str:
    value = (raw or "").strip().upper()
    if value in {"BUY", "CALL", "UP", "ARRIBA", "ALZA"}:
        return "UP"
    if value in {"SELL", "PUT", "DOWN", "ABAJO", "BAJA"}:
        return "DOWN"
    return "NEUTRAL"


def _ensure_runtime() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)


def _extract_json_from_text(payload: str) -> dict[str, Any]:
    if not payload.strip():
        return {}

    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    block = re.search(r"\{.*\}", payload, re.DOTALL)
    if not block:
        return {}

    try:
        data = json.loads(block.group(0))
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        return {}

    return {}


async def _await_if_needed(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


def _extract_text_blocks(message: Any) -> str:
    text_parts: list[str] = []
    content = getattr(message, "content", [])
    for block in content:
        block_any = cast(Any, block)
        if getattr(block_any, "type", None) != "text":
            continue
        text_value = getattr(block_any, "text", None)
        if isinstance(text_value, str) and text_value.strip():
            text_parts.append(text_value)
    return "\n".join(text_parts)


class PocketChartCapture:
    """Captura de chart reusable.

    En integración con main intenta usar la página ya abierta del broker para evitar
    conflictos de profile lock; si no existe, abre contexto propio como fallback.
    """

    def __init__(
        self,
        pocket_client: Any | None,
        chart_url: str,
        profile_dir: str = ".pocket_profile",
        headless: bool = False,
    ) -> None:
        self._pocket_client = pocket_client
        self._chart_url = chart_url
        self._profile_dir = (
            str((ROOT / profile_dir).resolve()) if not Path(profile_dir).is_absolute() else profile_dir
        )
        self._headless = headless
        self._playwright = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def _client_page(self) -> Page | None:
        if self._pocket_client is None:
            return None
        page = getattr(self._pocket_client, "_page", None)
        return page if isinstance(page, Page) else None

    async def connect(self) -> None:
        # Si el cliente principal ya tiene página, reutilizarla.
        if self._client_page() is not None:
            return
        if self._context is not None and self._page is not None:
            return

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self._profile_dir,
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.goto(self._chart_url, wait_until="load", timeout=60000)

    async def close(self) -> None:
        # Nunca cerrar la página/contexto del cliente principal.
        if self._context is not None:
            await self._context.close()
            self._context = None
            self._page = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def screenshot(
        self,
        asset: str,
        entry_time: datetime | None,
        message_id: int,
        delay_seconds: float = 0.0,
    ) -> Path:
        await self.connect()

        page = self._client_page() or self._page
        if page is None:
            raise RuntimeError("screenshot_error: chart_page_unavailable")

        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        ts = _now_utc().strftime("%Y%m%d_%H%M%S")
        asset_tag = _safe_filename(asset)
        time_tag = entry_time.strftime("%H%M") if entry_time else "NA"
        out = SNAP_DIR / f"{ts}_{asset_tag}_{time_tag}_msg{message_id}.png"
        await page.screenshot(path=str(out), full_page=True)
        return out


class VisionFilterGate:
    def __init__(
        self,
        pocket_client: Any,
        api_key: str,
        enabled: bool = True,
        model: str = "claude-sonnet-4-20250514",
        screenshot_delay_seconds: float = 3.0,
        timeout_seconds: float = 15.0,
        decision_log_path: Path = DEFAULT_DECISION_LOG,
        chart_url: str = "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        profile_dir: str = ".pocket_profile",
        headless: bool = False,
    ) -> None:
        self._enabled = bool(enabled)
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._screenshot_delay_seconds = max(0.0, float(screenshot_delay_seconds))
        self._decision_log_path = decision_log_path
        self._capture = PocketChartCapture(
            pocket_client=pocket_client,
            chart_url=chart_url,
            profile_dir=profile_dir,
            headless=headless,
        )
        self._anthropic_client = anthropic.Anthropic(api_key=api_key) if api_key else None
        _ensure_runtime()

    @staticmethod
    def from_env(pocket_client: Any) -> "VisionFilterGate":
        enabled = os.getenv("VISION_FILTER", "false").strip().lower() in {"1", "true", "yes", "on"}
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514").strip()
        screenshot_delay = float(os.getenv("VISION_SCREENSHOT_DELAY", "3") or "3")
        timeout_seconds = float(os.getenv("VISION_TIMEOUT", "15") or "15")
        chart_url = os.getenv(
            "POCKET_CHART_URL",
            "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        ).strip()
        profile_dir = os.getenv("POCKET_PROFILE_DIR", ".pocket_profile").strip()
        headless = os.getenv("POCKET_HEADLESS", "false").strip().lower() in {"1", "true", "yes", "on"}
        return VisionFilterGate(
            pocket_client=pocket_client,
            api_key=api_key,
            enabled=enabled,
            model=model,
            screenshot_delay_seconds=screenshot_delay,
            timeout_seconds=timeout_seconds,
            chart_url=chart_url,
            profile_dir=profile_dir,
            headless=headless,
        )

    async def close(self) -> None:
        await self._capture.close()

    async def analyze(
        self,
        *,
        asset: str,
        direction: str,
        entry_time_utc: datetime | None,
        message_id: int,
        equity_before: float | None,
    ) -> VisionDecision:
        signal_direction = _normalize_direction(direction)

        if not self._enabled:
            decision = VisionDecision(
                approved=True,
                reason="filter_disabled",
                fib_level=None,
                sr_zone="none",
            )
            self._append_decision_csv(
                asset=asset,
                direction=direction,
                approved=decision.approved,
                reason=decision.reason,
                fib_level=decision.fib_level,
                sr_zone=decision.sr_zone,
                equity_before=equity_before,
            )
            return decision

        if self._anthropic_client is None:
            decision = VisionDecision(False, "api_error", None, "none")
            self._append_decision_csv(
                asset=asset,
                direction=direction,
                approved=False,
                reason=decision.reason,
                fib_level=None,
                sr_zone="none",
                equity_before=equity_before,
            )
            return decision

        try:
            image_path = await self._capture.screenshot(
                asset=asset,
                entry_time=entry_time_utc,
                message_id=message_id,
                delay_seconds=self._screenshot_delay_seconds,
            )
        except Exception:
            decision = VisionDecision(False, "screenshot_error", None, "none")
            self._append_decision_csv(
                asset=asset,
                direction=direction,
                approved=False,
                reason=decision.reason,
                fib_level=None,
                sr_zone="none",
                equity_before=equity_before,
            )
            return decision

        try:
            raw_json = await asyncio.wait_for(
                asyncio.to_thread(self._ask_claude_for_json, image_path),
                timeout=self._timeout_seconds,
            )
        except Exception:
            decision = VisionDecision(False, "api_error", None, "none")
            self._append_decision_csv(
                asset=asset,
                direction=direction,
                approved=False,
                reason=decision.reason,
                fib_level=None,
                sr_zone="none",
                equity_before=equity_before,
            )
            return decision

        try:
            analysis = self._parse_analysis_json(raw_json)
        except ValueError:
            decision = VisionDecision(False, "parse_error", None, "none")
            self._append_decision_csv(
                asset=asset,
                direction=direction,
                approved=False,
                reason=decision.reason,
                fib_level=None,
                sr_zone="none",
                equity_before=equity_before,
            )
            return decision
        except Exception:
            decision = VisionDecision(False, "vision_filter_error", None, "none")
            self._append_decision_csv(
                asset=asset,
                direction=direction,
                approved=False,
                reason=decision.reason,
                fib_level=None,
                sr_zone="none",
                equity_before=equity_before,
            )
            return decision

        approved, reason, sr_zone = self._evaluate_gate(
            analysis=analysis,
            signal_direction=signal_direction,
        )

        decision = VisionDecision(
            approved=approved,
            reason=reason,
            fib_level=float(analysis.get("fib_618")) if analysis.get("fib_618") is not None else None,
            sr_zone=sr_zone,
        )

        self._append_decision_csv(
            asset=asset,
            direction=direction,
            approved=decision.approved,
            reason=decision.reason,
            fib_level=decision.fib_level,
            sr_zone=decision.sr_zone,
            equity_before=equity_before,
        )
        return decision

    def _ask_claude_for_json(self, image_path: Path) -> str:
        encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        message = self._anthropic_client.messages.create(
            model=self._model,
            max_tokens=900,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CLAUDE_PROMPT},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": encoded,
                            },
                        },
                    ],
                }
            ],
        )
        return _extract_text_blocks(message)

    def _parse_analysis_json(self, raw_text: str) -> dict[str, Any]:
        payload = _extract_json_from_text(raw_text)
        required = {
            "swing_high",
            "swing_low",
            "fib_618",
            "near_fib618",
            "sr_zone_type",
            "sr_zone_price",
            "near_sr",
            "bias",
            "approved",
            "reason",
        }
        if not payload or not required.issubset(payload.keys()):
            raise ValueError("malformed_json")
        return payload

    def _evaluate_gate(self, analysis: dict[str, Any], signal_direction: str) -> tuple[bool, str, str]:
        near_fib618 = bool(analysis.get("near_fib618", False))
        near_sr = bool(analysis.get("near_sr", False))
        sr_zone_type = str(analysis.get("sr_zone_type", "none")).strip().lower()
        sr_zone_price = analysis.get("sr_zone_price", 0.0)

        if signal_direction == "UP":
            sr_match = sr_zone_type == "support"
        elif signal_direction == "DOWN":
            sr_match = sr_zone_type == "resistance"
        else:
            sr_match = False

        hour_utc_minus_5 = (_now_utc().hour - 5) % 24
        hour_allowed = hour_utc_minus_5 not in {7, 21}

        approved = near_fib618 and near_sr and sr_match and hour_allowed

        reasons: list[str] = []
        if not near_fib618:
            reasons.append("not_near_fib618")
        if not near_sr:
            reasons.append("not_near_sr")
        if not sr_match:
            reasons.append("sr_bias_mismatch")
        if not hour_allowed:
            reasons.append(f"blocked_hour_utc_minus_5={hour_utc_minus_5}")

        reason = "approved" if approved else "|".join(reasons) if reasons else "vision_filter_error"
        sr_zone = f"{sr_zone_type}@{sr_zone_price}" if sr_zone_type != "none" else "none"
        return approved, reason, sr_zone

    def _append_decision_csv(
        self,
        *,
        asset: str,
        direction: str,
        approved: bool,
        reason: str,
        fib_level: float | None,
        sr_zone: str,
        equity_before: float | None,
    ) -> None:
        _ensure_runtime()
        write_header = not self._decision_log_path.exists()
        with self._decision_log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if write_header:
                writer.writerow(
                    [
                        "timestamp",
                        "asset",
                        "direction",
                        "approved",
                        "reason",
                        "fib_level",
                        "sr_zone",
                        "equity_before",
                    ]
                )
            writer.writerow(
                [
                    _now_utc().isoformat(),
                    asset,
                    direction,
                    bool(approved),
                    reason,
                    fib_level if fib_level is not None else "",
                    sr_zone,
                    equity_before if equity_before is not None else "",
                ]
            )


class VisionGatedExecutionEngine:
    """Wrapper mínimo: gate obligatorio antes de delegar al engine real."""

    def __init__(self, base_engine: Any, pocket_client: Any, gate: VisionFilterGate) -> None:
        self._base_engine = base_engine
        self._pocket_client = pocket_client
        self._gate = gate

    async def execute_signal(self, signal: Any) -> None:
        equity_before: float | None = None
        try:
            equity_before = await self._pocket_client.get_account_balance()
        except Exception:
            equity_before = None

        decision = await self._gate.analyze(
            asset=str(signal.asset),
            direction=str(signal.side),
            entry_time_utc=getattr(signal, "execute_at_utc", None),
            message_id=int(getattr(signal, "message_id", 0) or 0),
            equity_before=equity_before,
        )

        if not decision.approved:
            logging.info(
                "[VisionFilter] REJECT asset=%s dir=%s reason=%s",
                str(signal.asset),
                str(signal.side),
                decision.reason,
            )
            return

        logging.info(
            "[VisionFilter] APPROVE asset=%s dir=%s",
            str(signal.asset),
            str(signal.side),
        )

        await self._base_engine.execute_signal(signal)


# --- Modo standalone opcional para pruebas del filtro en aislamiento ---

def _load_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value


async def run_listener() -> None:
    load_dotenv()
    _ensure_runtime()

    api_id = int(_load_required_env("TELEGRAM_API_ID"))
    api_hash = _load_required_env("TELEGRAM_API_HASH")
    source_chats = [c.strip() for c in _load_required_env("TELEGRAM_SOURCE_CHATS").split(",") if c.strip()]
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "signal_reader")

    parser = SignalParser(default_amount=1.0, signal_tz_offset_hours=-3, signal_timezone="America/Argentina/Buenos_Aires")
    gate = VisionFilterGate.from_env(pocket_client=None)

    client = TelegramClient(session_name, api_id, api_hash)
    await _await_if_needed(client.start())
    entities = [await client.get_entity(source) for source in source_chats]

    @client.on(events.NewMessage(chats=entities))
    async def _handler(event: events.NewMessage.Event) -> None:
        text = (event.raw_text or "").strip()
        if not text:
            return

        parsed = parser.parse(text, received_at_utc=_now_utc())
        if parsed is None:
            return

        decision = await gate.analyze(
            asset=parsed.asset,
            direction=parsed.side,
            entry_time_utc=parsed.execute_at_utc,
            message_id=event.id,
            equity_before=None,
        )

        print(
            f"[VisionFilter] msg={event.id} asset={parsed.asset} dir={parsed.side} "
            f"approved={decision.approved} reason={decision.reason}"
        )

    print("[VisionFilter] Listener activo (modo standalone).")
    try:
        await _await_if_needed(client.run_until_disconnected())
    finally:
        await gate.close()
        await _await_if_needed(client.disconnect())


if __name__ == "__main__":
    asyncio.run(run_listener())
