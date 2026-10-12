import os
from dotenv import load_dotenv
from utils.options_settings import load_options_settings

load_dotenv()

# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
COMMAND_PREFIX: str = "!"

# Channel IDs for scheduled pushes (set in .env)
ALERT_CHANNEL_ID: int = int(os.getenv("ALERT_CHANNEL_ID", 0))
MARKET_CHANNEL_ID: int = int(os.getenv("MARKET_CHANNEL_ID", 0))
NEWS_CHANNEL_ID: int = int(os.getenv("NEWS_CHANNEL_ID", 0))
# Options-flow anomaly alerts. Falls back to ALERT_CHANNEL_ID when unset.
OPTIONS_ALERT_CHANNEL_ID: int = int(os.getenv("OPTIONS_ALERT_CHANNEL_ID", 0)) or ALERT_CHANNEL_ID

# ---------------------------------------------------------------------------
# External APIs
# ---------------------------------------------------------------------------
NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")
FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
FMP_API_KEY:  str = os.getenv("FMP_API_KEY",  "")
JIN10_API_KEY: str = os.getenv("JIN10_API_KEY", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-flash")
LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))
LLM_REASONING_ENABLED: bool = os.getenv("LLM_REASONING_ENABLED", "false").lower() in ("1", "true", "yes")

# SEC EDGAR requires a descriptive User-Agent (name + contact email) on every
# request, or it rejects the call outright. No API key needed.
SEC_EDGAR_USER_AGENT: str = os.getenv(
    "SEC_EDGAR_USER_AGENT", "DiscordStockBot/1.0 (yhhua@berkeley.edu)"
)

# ---------------------------------------------------------------------------
# moomoo OpenAPI (options-flow anomaly detection)
# ---------------------------------------------------------------------------
# Nonsecret options settings live in version-controlled settings/options.json.
# Legacy options environment variables intentionally no longer override the file.
_options = load_options_settings()
_moomoo, _scan, _catalyst = (_options[k] for k in ("moomoo", "scan", "catalyst"))
MOOMOO_ENABLED = _moomoo["enabled"]
MOOMOO_HOST = _moomoo["host"]
MOOMOO_PORT = _moomoo["port"]
MOOMOO_TRANSPORT = _moomoo["transport"]
MOOMOO_APP_KEY = os.getenv("MOOMOO_APP_KEY", "")
MOOMOO_PRIVATE_KEY_PEM = os.getenv("MOOMOO_PRIVATE_KEY_PEM", "")
MOOMOO_REQUEST_INTERVAL = _moomoo["request_interval_seconds"]
MOOMOO_MAX_PAGES = _moomoo["max_pages"]
MOOMOO_SCAN_TIMEOUT_SECONDS = _moomoo["scan_timeout_seconds"]
MOOMOO_SCREEN_FIELD_FILTER = _moomoo["field_filter"]
OPTIONS_UNIVERSE = _scan["universe"]
OPTIONS_WATCHLIST = _scan["watchlist"]
OPTIONS_EXCLUDED_UNDERLYINGS = frozenset(_scan["excluded_underlyings"])
OPTIONS_SCAN_MINUTES = _scan["interval_minutes"]
OPTIONS_MIN_PREMIUM = _scan["min_premium"]
OPTIONS_MIN_CONTRACT_VOLUME = _scan["min_contract_volume"]
OPTIONS_MAX_DTE = _scan["max_dte"]
OPTIONS_MAX_SPREAD_RATIO = _scan["max_spread_ratio"]
OPTIONS_MAX_QUOTE_AGE_SECONDS = _scan["max_quote_age_seconds"]
OPTIONS_L2_SCORE = _scan["l2_score"]
OPTIONS_L3_SCORE = _scan["l3_score"]
OPTIONS_MIN_HISTORY_DAYS = _scan["min_history_days"]
OPTIONS_MIN_RELATIVE_VOLUME = _scan["min_relative_volume"]
OPTIONS_MIN_IV_JUMP = _scan["min_iv_jump"]
OPTIONS_SHORT_DTE = _scan["short_dte"]
OPTIONS_SCORE_WEIGHTS = _scan["score_weights"]
OPTIONS_EXECUTION_WINDOW_SECONDS = _scan["execution_window_seconds"]
OPTIONS_REPEAT_COUNT = _scan["repeat_count"]
OPTIONS_RETAIN_SNAPSHOTS_DAYS = _scan["retain_snapshots_days"]
OPTIONS_EVENT_COOLDOWN_MINUTES = _scan["event_cooldown_minutes"]
OPTIONS_MAX_ALERTS_PER_SCAN = _scan["max_alerts_per_scan"]

CATALYST_ENABLED = _catalyst["enabled"]
CATALYST_RESEARCH_MODEL = _catalyst["research_model"]
CATALYST_SEARCH_PROVIDER = _catalyst["search_provider"]
CATALYST_JUDGE_MODE = _catalyst["judge_mode"]
CATALYST_JEV_MODEL = _catalyst["jev_model"]
CATALYST_CONTEXT = _catalyst["company_context"]
CATALYST_CACHE_MINUTES = _catalyst["cache_minutes"]
CATALYST_LOOKBACK_DAYS = _catalyst["lookback_days"]
CATALYST_MAX_SEARCHES = _catalyst["max_searches"]
CATALYST_MAX_RESULTS = _catalyst["max_results"]
CATALYST_DAILY_REQUEST_LIMIT = _catalyst["daily_request_limit"]
CATALYST_DAILY_BUDGET_USD = _catalyst["daily_budget_usd"]
CATALYST_CALL_RESERVE_USD = _catalyst["call_reserve_usd"]
CATALYST_MAX_INPUT_BYTES = _catalyst["max_input_bytes"]
CATALYST_MAX_OUTPUT_TOKENS = _catalyst["max_output_tokens"]
CATALYST_TIMEOUT_SECONDS = _catalyst["timeout_seconds"]
CATALYST_MAX_CONCURRENCY = _catalyst["max_concurrency"]

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
DATA_FOOTER: str = "Data via Yahoo Finance  |  prices may be delayed up to 15 min"
