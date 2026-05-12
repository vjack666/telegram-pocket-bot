import os
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

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
    telegram_restart_after_signal: bool
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
    masaniello_n_ops: int
    masaniello_w_needed: int
    masaniello_base_balance: float
    masaniello_max_session_losses: int   # max perdidas antes de cortar sesion
    # ── Sizing global ──────────────────────────────────────────────────────
    max_trade_pct: float          # cap por operación: ej 0.10 = 10% de base
    max_total_exposure_pct: float # cap exposición total — placeholder RiskEngine
    calc_base_balance: float      # balance fijo para calculator (base fija)
    recovery_g1_mult: float       # 0.0 = auto desde payout
    recovery_g2_mult: float       # 0.0 = auto desde payout
    # ── Capital operativo dinámico (equity bands) ──────────────────────────
    equity_bands_enabled: bool                  # activar sistema de bandas
    equity_bands: List[Tuple[float, float]]     # [(min_balance, base), ...]
    equity_band_upgrade_sessions: int           # sesiones antes de subir banda
    equity_daily_target_pct: float              # meta diaria = base × pct
    equity_state_persist: bool                  # persistir estado en disco
    equity_state_path: str                      # ruta del json de estado
    equity_deposit_guard_enabled: bool          # detectar +equity externo
    equity_deposit_jump_pct: float              # salto relativo para depósito
    equity_deposit_cooldown_sessions: int       # cooldown de upgrades
    # ── Daily Profit Tracking ──────────────────────────────────────────────
    daily_profit_tracking_enabled: bool         # activar tracking diario
    daily_profit_target: float                  # meta diaria en dinero ($60)
    daily_profit_defensive_mode: bool           # cambiar a defensive tras meta
    daily_profit_state_path: str                # ruta del json de estado diario
    # ── MasanielloManager (caja negra de stake) ────────────────────────────
    masaniello_manager_session_base: float      # capital por sesión (caja)
    masaniello_manager_ops_total: int           # operaciones por sesión
    masaniello_manager_wins_needed: int         # ITM necesarios para ganar sesión
    masaniello_manager_max_gale_mult: int       # techo Macro-Gale (ej. 16 = x1..x16)
    # ── Modo Híbrido (Bot + Humano) ────────────────────────────────────────
    g2_human_approval: bool          # True = pausa en G2 y espera APROBAR/CANCELAR del usuario
    g2_approval_timeout_seconds: int # segundos para responder antes de cancelar automáticamente
    session_learning_db_path: str    # ruta del JSONL de aprendizaje de sesiones
    operation_mode_schedule_enabled: bool  # alterna HIBRIDO/AUTOMATICO por horario
    operation_mode_hybrid_start_hour: int  # inicio HIBRIDO en hora de señales (UTC-3)
    operation_mode_hybrid_end_hour: int    # fin HIBRIDO en hora de señales (UTC-3)
    operation_mode_sound_alert: bool       # alerta sonora cuando cambia el modo
    # ── Manual Operations ──────────────────────────────────────────────────
    manual_operations_enabled: bool        # activar registro de operaciones manuales

    @staticmethod
    def load() -> "AppSettings":
        # Carga .env de usuario (AppData) antes del .env local.
        # Si ambos existen, el primero cargado mantiene prioridad porque override=False.
        appdata = os.getenv("APPDATA", "").strip()
        if appdata:
            user_env = Path(appdata) / "PocketOptionBot" / ".env"
            if user_env.exists():
                load_dotenv(dotenv_path=user_env, override=False)

        load_dotenv(override=False)

        api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        session_name = os.getenv("TELEGRAM_SESSION_NAME", "signal_reader").strip()
        source_chats = _csv_list(os.getenv("TELEGRAM_SOURCE_CHATS", ""))
        enable_telegram_flag = _to_bool(os.getenv("APP_ENABLE_TELEGRAM", "false"))
        # Si hay credenciales completas, activar Telegram aunque la plantilla haya quedado en false.
        has_telegram_config = bool(api_id_raw and api_hash and source_chats)
        enable_telegram = enable_telegram_flag or has_telegram_config

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
            telegram_restart_after_signal=_to_bool(
                os.getenv("APP_TELEGRAM_RESTART_AFTER_SIGNAL", "false")
            ),
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
            pocket_profile_dir=_resolve_profile_dir(
                os.getenv("POCKET_PROFILE_DIR", ".pocket_profile")
            ),
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
            masaniello_n_ops=int(os.getenv("APP_MASANIELLO_N_OPS", "12")),
            masaniello_w_needed=int(os.getenv("APP_MASANIELLO_W_NEEDED", "4")),
            masaniello_base_balance=float(os.getenv("APP_MASANIELLO_BASE_BALANCE", "300")),
            masaniello_max_session_losses=int(os.getenv("APP_MASANIELLO_MAX_SESSION_LOSSES", "3")),
            max_trade_pct=float(os.getenv("APP_MAX_TRADE_PCT", "0.10")),
            max_total_exposure_pct=float(os.getenv("APP_MAX_TOTAL_EXPOSURE_PCT", "0.25")),
            calc_base_balance=float(os.getenv("APP_CALC_BASE_BALANCE", "300")),
            recovery_g1_mult=float(os.getenv("APP_RECOVERY_G1_MULT", "").strip() or "0"),
            recovery_g2_mult=float(os.getenv("APP_RECOVERY_G2_MULT", "").strip() or "0"),
            equity_bands_enabled=_to_bool(os.getenv("APP_EQUITY_BANDS_ENABLED", "false")),
            equity_bands=_parse_equity_bands(
                os.getenv("APP_EQUITY_BANDS", "0:300,400:500,700:800,1200:1500")
            ),
            equity_band_upgrade_sessions=int(
                os.getenv("APP_EQUITY_BAND_UPGRADE_SESSIONS", "3")
            ),
            equity_daily_target_pct=float(
                os.getenv("APP_EQUITY_DAILY_TARGET_PCT", "0.20")
            ),
            equity_state_persist=_to_bool(
                os.getenv("APP_EQUITY_STATE_PERSIST", "true")
            ),
            equity_state_path=os.getenv(
                "APP_EQUITY_STATE_PATH",
                "runtime/equity_bands_state.json",
            ).strip(),
            equity_deposit_guard_enabled=_to_bool(
                os.getenv("APP_EQUITY_DEPOSIT_GUARD_ENABLED", "false")
            ),
            equity_deposit_jump_pct=float(
                os.getenv("APP_EQUITY_DEPOSIT_JUMP_PCT", "0.60")
            ),
            equity_deposit_cooldown_sessions=int(
                os.getenv("APP_EQUITY_DEPOSIT_COOLDOWN_SESSIONS", "3")
            ),
            daily_profit_tracking_enabled=_to_bool(
                os.getenv("APP_DAILY_PROFIT_TRACKING_ENABLED", "false")
            ),
            daily_profit_target=float(
                os.getenv("APP_DAILY_PROFIT_TARGET", "60.0")
            ),
            daily_profit_defensive_mode=_to_bool(
                os.getenv("APP_DAILY_PROFIT_DEFENSIVE_MODE", "true")
            ),
            daily_profit_state_path=os.getenv(
                "APP_DAILY_PROFIT_STATE_PATH",
                "runtime/daily_profit_state.json",
            ).strip(),
            masaniello_manager_session_base=float(
                os.getenv("APP_MASANIELLO_MANAGER_SESSION_BASE", "10.0")
            ),
            masaniello_manager_ops_total=int(
                os.getenv("APP_MASANIELLO_MANAGER_OPS_TOTAL", "6")
            ),
            masaniello_manager_wins_needed=int(
                os.getenv("APP_MASANIELLO_MANAGER_WINS_NEEDED", "3")
            ),
            masaniello_manager_max_gale_mult=int(
                os.getenv("APP_MASANIELLO_MANAGER_MAX_GALE_MULT", "16")
            ),
            g2_human_approval=_to_bool(os.getenv("APP_G2_HUMAN_APPROVAL", "false")),
            g2_approval_timeout_seconds=int(os.getenv("APP_G2_APPROVAL_TIMEOUT_SECONDS", "20")),
            session_learning_db_path=os.getenv(
                "APP_SESSION_LEARNING_DB_PATH",
                "runtime/session_learning.jsonl",
            ).strip(),
            operation_mode_schedule_enabled=_to_bool(
                os.getenv("APP_OPERATION_MODE_SCHEDULE_ENABLED", "true")
            ),
            operation_mode_hybrid_start_hour=int(
                os.getenv("APP_OPERATION_MODE_HYBRID_START_HOUR", "10")
            ),
            operation_mode_hybrid_end_hour=int(
                os.getenv("APP_OPERATION_MODE_HYBRID_END_HOUR", "21")
            ),
            operation_mode_sound_alert=_to_bool(
                os.getenv("APP_OPERATION_MODE_SOUND_ALERT", "true")
            ),
            manual_operations_enabled=_to_bool(
                os.getenv("APP_MANUAL_OPERATIONS_ENABLED", "false")
            ),
        )


def _csv_list(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_profile_dir(raw: str) -> str:
    """Resuelve POCKET_PROFILE_DIR a una ruta escribible.

    - Si se da ruta absoluta, se respeta.
    - Si es relativa y no se puede escribir (p.ej. Program Files), usa AppData.
    """
    clean = (raw or "").strip() or ".pocket_profile"
    candidate = Path(clean)

    if candidate.is_absolute():
        return str(candidate)

    # Intentar usar la ruta relativa configurada.
    try:
        resolved = candidate.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return str(resolved)
    except Exception:
        appdata = os.getenv("APPDATA", "").strip()
        if appdata:
            fallback = Path(appdata) / "PocketOptionBot" / "browser_profile"
        else:
            fallback = Path.home() / ".pocketoptionbot" / "browser_profile"
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)


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
        # Use the last ':' so URL keys like https://t.me/... are preserved.
        colon_idx = entry.rfind(":")
        if colon_idx <= 0 or colon_idx >= len(entry) - 1:
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


def _parse_equity_bands(raw: str) -> List[Tuple[float, float]]:
    """Parsea 'min:base,min:base,...' → lista de tuplas ordenadas ascendente.

    Ejemplo: '0:300,400:500,700:800,1200:1500'
    Retorna al menos [(0, 300)] ante cualquier error de parseo.
    """
    result: List[Tuple[float, float]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 2:
            continue
        try:
            min_b = float(parts[0].strip())
            base = float(parts[1].strip())
        except ValueError:
            continue
        if base > 0:
            result.append((min_b, base))
    if not result:
        result = [(0.0, 300.0)]
    return sorted(result, key=lambda x: x[0])
