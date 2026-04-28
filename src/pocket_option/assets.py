import re
from typing import Dict

# Canonical asset labels used when sending orders/searching in Pocket Option.
SUPPORTED_POCKET_OPTION_ASSETS = (
    "EURUSD OTC",
    "GBPUSD OTC",
    "USDJPY OTC",
    "USDCHF OTC",
    "AUDUSD OTC",
    "USDCAD OTC",
    "NZDUSD OTC",
    "EURJPY OTC",
    "EURGBP OTC",
    "EURCHF OTC",
    "GBPJPY OTC",
    "GBPCHF OTC",
    "AUDJPY OTC",
    "AUDCAD OTC",
    "AUDCHF OTC",
    "CADJPY OTC",
    "CHFJPY OTC",
    "NZDJPY OTC",
    "EURAUD OTC",
    "EURNZD OTC",
    "GBPAUD OTC",
    "GBPCAD OTC",
    "GBPNZD OTC",
    "NZDCAD OTC",
    "NZDCHF OTC",
    "XAUUSD OTC",
    "BTCUSDT OTC",
)


def _normalize_key(raw: str) -> str:
    key = (raw or "").upper().strip()
    key = re.sub(r"[^A-Z0-9]", "", key)
    return key


def _build_aliases() -> Dict[str, str]:
    aliases: Dict[str, str] = {
        "GOLD": "XAUUSD OTC",
        "XAUUSD": "XAUUSD OTC",
        "XAUUSDOTC": "XAUUSD OTC",
        "BTCUSDT": "BTCUSDT OTC",
        "BTCUSD": "BTCUSDT OTC",
        "BTCUSDTOTC": "BTCUSDT OTC",
    }

    for canonical in SUPPORTED_POCKET_OPTION_ASSETS:
        clean = canonical.replace(" OTC", "")
        aliases[_normalize_key(canonical)] = canonical
        aliases[_normalize_key(clean)] = canonical

    return aliases


_ASSET_ALIASES = _build_aliases()


def normalize_asset_for_compare(asset: str) -> str:
    txt = (asset or "").upper().strip()
    txt = re.sub(r"\bOTC(?:\s+OTC)+\b", "OTC", txt)
    txt = re.sub(r"\bOTC\b", "", txt)
    txt = re.sub(r"[^A-Z0-9]", "", txt)
    return txt.strip()


def canonicalize_pocket_asset(raw_asset: str, default_asset: str = "EURUSD OTC") -> str:
    normalized = _normalize_key(raw_asset)
    if not normalized:
        return default_asset

    found = _ASSET_ALIASES.get(normalized)
    if found:
        return found

    # Fallback: canonicaliza texto libre (slashes, espacios y OTC duplicado).
    txt = " ".join((raw_asset or "").upper().split())
    txt = re.sub(r"\bOTC(?:\s+OTC)+\b", "OTC", txt)
    has_otc = "OTC" in txt
    base = normalize_asset_for_compare(txt)
    if not base:
        return default_asset

    if has_otc:
        return f"{base} OTC"
    return base
