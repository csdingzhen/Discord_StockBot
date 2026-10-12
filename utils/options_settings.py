"""Versioned, repo-relative options configuration. Secrets never belong here."""
import json
import math
import re
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "settings" / "options.json"
_WEIGHTS = ("vol_gt_oi", "premium_activity", "iv_jump", "relative_volume",
            "short_dte", "interval_activity", "aggressive_buying", "repeated_buying")
# Numeric rules are (minimum, maximum, type); string tuples are enums.
_SCHEMA = {
    "version": (1, 1, int),
    "moomoo": {
        "enabled": bool, "transport": ("rest", "opend"), "host": str,
        "port": (1, 65535, int), "request_interval_seconds": (0.1, 60, float),
        "max_pages": (1, 100, int), "scan_timeout_seconds": (5, 240, int),
        "field_filter": dict,
    },
    "scan": {
        "universe": ("market", "watchlist"), "watchlist": list, "excluded_underlyings": list,
        "interval_minutes": (1, 60, int), "min_premium": (0, 1e12, float),
        "min_contract_volume": (1, 1e9, int), "max_dte": (1, 3650, int),
        "max_spread_ratio": (0, 2, float), "max_quote_age_seconds": (1, 86400, int),
        "l2_score": (1, 100, int), "l3_score": (1, 100, int),
        "min_history_days": (2, 30, int), "min_relative_volume": (1, 10000, float),
        "min_iv_jump": (0, 10000, float), "short_dte": (0, 3650, int),
        "score_weights": {k: (0, 100, float) for k in _WEIGHTS},
        "execution_window_seconds": (1, 86400, int), "repeat_count": (2, 10000, int),
        "retain_snapshots_days": (31, 3650, int), "event_cooldown_minutes": (1, 1440, int),
        "max_alerts_per_scan": (1, 20, int),
    },
    "catalyst": {
        "enabled": bool, "research_model": str, "search_provider": ("none", "openrouter"),
        "judge_mode": ("jev", "deepseek"), "jev_model": str, "company_context": dict,
        "cache_minutes": (1, 1440, int), "lookback_days": (1, 30, int),
        "max_searches": (0, 2, int), "max_results": (1, 5, int),
        "daily_request_limit": (0, 10000, int), "daily_budget_usd": (0, 10000, float),
        "call_reserve_usd": (0.01, 10000, float), "max_input_bytes": (1000, 24000, int),
        "max_output_tokens": (128, 4096, int), "timeout_seconds": (5, 120, int),
        "max_concurrency": (1, 4, int),
    },
}


def _validate(value, rule, path):
    if isinstance(rule, dict):
        if not isinstance(value, dict) or value.keys() != rule.keys():
            missing = sorted(rule.keys() - value.keys()) if isinstance(value, dict) else sorted(rule)
            extra = sorted(value.keys() - rule.keys()) if isinstance(value, dict) else []
            raise ValueError(f"{path}: missing keys {missing}; unknown keys {extra}")
        for key, child in rule.items():
            _validate(value[key], child, f"{path}.{key}")
        return
    if isinstance(rule, tuple):
        if isinstance(rule[0], str):
            valid = isinstance(value, str) and value in rule
        else:
            low, high, kind = rule
            valid = (type(value) in ((int,) if kind is int else (int, float))
                     and math.isfinite(value) and low <= value <= high)
    else:
        valid = type(value) is rule and (rule is not str or bool(value.strip()))
    if not valid:
        raise ValueError(f"{path}: invalid type or value")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate options setting: {key}")
        result[key] = value
    return result


def load_options_settings(path=DEFAULT_PATH):
    try:
        with Path(path).open(encoding="utf-8") as file:
            settings = json.load(file, object_pairs_hook=_unique_object)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot load options settings at {path}: {exc}") from exc
    _validate(settings, _SCHEMA, "options")
    scan, catalyst = settings["scan"], settings["catalyst"]
    for key in ("watchlist", "excluded_underlyings"):
        if any(not isinstance(s, str) or not re.fullmatch(r"[A-Z.][A-Z0-9.]{0,14}", s)
               for s in scan[key]):
            raise ValueError(f"options.scan.{key}: use uppercase US ticker symbols")
    if scan["l2_score"] > scan["l3_score"]:
        raise ValueError("options.scan: l2_score must not exceed l3_score")
    if scan["short_dte"] > scan["max_dte"]:
        raise ValueError("options.scan: short_dte must not exceed max_dte")
    if scan["universe"] == "watchlist" and not scan["watchlist"]:
        raise ValueError("options.scan.watchlist must not be empty in watchlist mode")
    if settings["moomoo"]["transport"] == "opend" and scan["universe"] == "market":
        raise ValueError("Market-wide screening requires moomoo.transport=rest")
    for ticker, context in catalyst["company_context"].items():
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,14}", ticker):
            raise ValueError("options.catalyst.company_context: invalid ticker")
        _validate(context, {"aliases": list, "topics": list}, f"company_context.{ticker}")
        if any(not isinstance(s, str) or not s.strip() for values in context.values() for s in values):
            raise ValueError("Company aliases/topics must be nonempty strings")
    return settings
