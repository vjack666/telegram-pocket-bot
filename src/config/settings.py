import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppSettings:
    enable_telegram: bool
    telegram_api_id: Optional[int]
    telegram_api_hash: str
    telegram_session_name: str
    telegram_source_chats: List[str]
    telegram_backfill_minutes: float
    telegram_backfill_limit: int
    telegram_realtime_only: bool
    single_asset_mode: bool
    martingale_amounts: List[float]
    martingale_mode: str
    calc_payout_percent: float
    calc_increment: int
    calc_rule10_balance_threshold: float
    calc_max_steps: int
    order_result_grace_seconds: int
    color_output: bool
    pocket_account_mode: str
    pocket_demo_url: str
    pocket_profile_dir: str
    pocket_headless: bool
    pocket_execute_orders: bool
    pocket_max_order_amount: float
    pocket_balance_selector: str
    pocket_asset_open_selector: str
    pocket_asset_search_selector: str
    pocket_asset_result_selector: str
    pocket_buy_selector: str
    pocket_sell_selector: str
    pocket_amount_selector: str
    pocket_balance_wait_seconds: int
    pocket_keep_browser_open: bool
    default_amount: float
    default_asset: str
    override_asset: str
    override_side: Optional[str]
    dry_run: bool
    log_level: str
    expected_utc_offset_hours: int
    enforce_expected_utc_offset: bool
    signal_timezone: str
    signal_late_tolerance_seconds: int
    processing_queue_maxsize: int
    message_dedupe_ttl_seconds: int
    busy_policy: str
    telegram_channel_names: Dict[str, str]  # raw_chat -> display name

    @staticmethod
    def load() -> "AppSettings":
        load_dotenv()

        api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        session_name = os.getenv("TELEGRAM_SESSION_NAME", "signal_reader").strip()
        source_chats = _csv_list(os.getenv("TELEGRAM_SOURCE_CHATS", ""))
        enable_telegram = _to_bool(os.getenv("APP_ENABLE_TELEGRAM", "false"))

        if enable_telegram:
            if not api_id_raw:
                raise ValueError("Falta TELEGRAM_API_ID en .env")
            if not api_hash:
                raise ValueError("Falta TELEGRAM_API_HASH en .env")
            if not source_chats:
                raise ValueError("Falta TELEGRAM_SOURCE_CHATS en .env")

        return AppSettings(
            enable_telegram=enable_telegram,
            telegram_api_id=int(api_id_raw) if api_id_raw else None,
            telegram_api_hash=api_hash,
            telegram_session_name=session_name,
            telegram_source_chats=source_chats,
            telegram_backfill_minutes=_parse_backfill_minutes(
                os.getenv("TELEGRAM_BACKFILL_MINUTES", "15"),
                os.getenv("TELEGRAM_BACKFILL_SECONDS", ""),
            ),
            telegram_backfill_limit=int(os.getenv("TELEGRAM_BACKFILL_LIMIT", "40")),
            telegram_realtime_only=_to_bool(os.getenv("APP_TELEGRAM_REALTIME_ONLY", "false")),
            single_asset_mode=_to_bool(os.getenv("APP_SINGLE_ASSET_MODE", "false")),
            martingale_amounts=_csv_float_list(
                os.getenv("APP_MARTINGALE_AMOUNTS", "2,4,10"),
                fallback=[2.0, 4.0, 10.0],
            ),
            martingale_mode=os.getenv("APP_MARTINGALE_MODE", "fixed").strip().lower(),
            calc_payout_percent=float(os.getenv("APP_CALC_PAYOUT_PERCENT", "92")),
            calc_increment=int(os.getenv("APP_CALC_INCREMENT", "2")),
            calc_rule10_balance_threshold=float(
                os.getenv("APP_CALC_RULE10_BALANCE_THRESHOLD", "50")
            ),
            calc_max_steps=int(os.getenv("APP_CALC_MAX_STEPS", "3")),
            order_result_grace_seconds=int(os.getenv("APP_ORDER_RESULT_GRACE_SECONDS", "15")),
            color_output=_to_bool(os.getenv("APP_COLOR_OUTPUT", "true")),
            pocket_account_mode=os.getenv("POCKET_ACCOUNT_MODE", "demo").strip().lower(),
            pocket_demo_url=os.getenv(
                "POCKET_DEMO_URL",
                "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
            ).strip(),
            pocket_profile_dir=os.getenv("POCKET_PROFILE_DIR", ".pocket_profile").strip(),
            pocket_headless=_to_bool(os.getenv("POCKET_HEADLESS", "false")),
            pocket_execute_orders=_to_bool(os.getenv("POCKET_EXECUTE_ORDERS", "false")),
            pocket_max_order_amount=float(os.getenv("POCKET_MAX_ORDER_AMOUNT", "5")),
            pocket_balance_selector=os.getenv("POCKET_BALANCE_SELECTOR", "").strip(),
            pocket_asset_open_selector=os.getenv("POCKET_ASSET_OPEN_SELECTOR", "").strip(),
            pocket_asset_search_selector=os.getenv("POCKET_ASSET_SEARCH_SELECTOR", "").strip(),
            pocket_asset_result_selector=os.getenv("POCKET_ASSET_RESULT_SELECTOR", "").strip(),
            pocket_buy_selector=os.getenv("POCKET_BUY_SELECTOR", "").strip(),
            pocket_sell_selector=os.getenv("POCKET_SELL_SELECTOR", "").strip(),
            pocket_amount_selector=os.getenv("POCKET_AMOUNT_SELECTOR", "").strip(),
            pocket_balance_wait_seconds=int(os.getenv("POCKET_BALANCE_WAIT_SECONDS", "240")),
            pocket_keep_browser_open=_to_bool(os.getenv("POCKET_KEEP_BROWSER_OPEN", "true")),
            default_amount=float(os.getenv("APP_DEFAULT_AMOUNT", "1.0")),
            default_asset=os.getenv("APP_DEFAULT_ASSET", "EURUSD OTC").strip().upper(),
            override_asset=os.getenv("APP_OVERRIDE_ASSET", "").strip().upper(),
            override_side=_normalize_side_override(os.getenv("APP_OVERRIDE_SIDE", "")),
            dry_run=_to_bool(os.getenv("APP_DRY_RUN", "true")),
            log_level=os.getenv("APP_LOG_LEVEL", "INFO").strip().upper(),
            expected_utc_offset_hours=int(os.getenv("APP_EXPECTED_UTC_OFFSET_HOURS", "-3")),
            enforce_expected_utc_offset=_to_bool(os.getenv("APP_ENFORCE_UTC_OFFSET", "true")),
            signal_timezone=os.getenv(
                "APP_SIGNAL_TIMEZONE",
                "America/Argentina/Buenos_Aires",
            ).strip(),
            signal_late_tolerance_seconds=int(os.getenv("APP_SIGNAL_LATE_TOLERANCE_SECONDS", "300")),
            processing_queue_maxsize=int(os.getenv("APP_PROCESSING_QUEUE_MAXSIZE", "500")),
            message_dedupe_ttl_seconds=int(os.getenv("APP_MESSAGE_DEDUPE_TTL_SECONDS", "21600")),
            busy_policy=os.getenv("APP_BUSY_POLICY", "queue").strip().lower(),
            telegram_channel_names=_parse_channel_names(
                os.getenv("TELEGRAM_CHANNEL_NAMES", "")
            ),
        )


def _csv_list(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _csv_float_list(raw: str, fallback: List[float]) -> List[float]:
    values: List[float] = []
    for token in raw.split(","):
        clean = token.strip()
        if not clean:
            continue
        try:
            num = float(clean)
        except ValueError:
            continue
        if num > 0:
            values.append(num)

    if values:
        return values
    return fallback


def _to_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_channel_names(raw: str) -> Dict[str, str]:
    """Parsea TELEGRAM_CHANNEL_NAMES=chat_key:Nombre Canal|chat_key2:Otro Canal.

    El separador entre pares es '|', y entre clave y nombre es ':'.
    Ejemplo:
        TELEGRAM_CHANNEL_NAMES=@viptrader:VIP TRADER A|https://t.me/...:SMART SIGNALS
    """
    result: Dict[str, str] = {}
    for entry in raw.split("|"):
        entry = entry.strip()
        if not entry:
            continue
        colon_idx = entry.find(":")
        if colon_idx <= 0:
            continue
        key = entry[:colon_idx].strip()
        name = entry[colon_idx + 1:].strip()
        if key and name:
            result[key] = name
    return result


def _parse_backfill_minutes(raw_minutes: str, raw_seconds: str) -> float:
    # If TELEGRAM_BACKFILL_SECONDS is present, it takes precedence for fine-grained control.
    seconds_txt = (raw_seconds or "").strip()
    if seconds_txt:
        try:
            seconds_val = float(seconds_txt)
            return max(0.0, seconds_val / 60.0)
        except ValueError:
            pass

    minutes_txt = (raw_minutes or "").strip()
    try:
        return max(0.0, float(minutes_txt))
    except ValueError:
        return 15.0


def _normalize_side_override(raw: str) -> Optional[str]:
    side = raw.strip().upper()
    if side in {"BUY", "SELL"}:
        return side
    return None
