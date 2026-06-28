import os
from dotenv import load_dotenv

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
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
FMP_API_KEY:  str = os.getenv("FMP_API_KEY",  "")
JIN10_API_KEY: str = os.getenv("JIN10_API_KEY", "")
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")

# SEC EDGAR requires a descriptive User-Agent (name + contact email) on every
# request, or it rejects the call outright. No API key needed.
SEC_EDGAR_USER_AGENT: str = os.getenv(
    "SEC_EDGAR_USER_AGENT", "DiscordStockBot/1.0 (yhhua@berkeley.edu)"
)

# ---------------------------------------------------------------------------
# moomoo OpenAPI (options-flow anomaly detection)
# ---------------------------------------------------------------------------
# Requires moomoo OpenD running locally (or reachable) and logged into the
# account. The bot talks to OpenD over TCP; OpenD talks to moomoo's servers.
# Disabled by default so the rest of the bot runs without OpenD installed --
# flip MOOMOO_ENABLED=true in .env on the machine where OpenD is set up.
MOOMOO_ENABLED: bool = os.getenv("MOOMOO_ENABLED", "false").lower() in ("1", "true", "yes")
MOOMOO_HOST: str = os.getenv("MOOMOO_HOST", "127.0.0.1")
MOOMOO_PORT: int = int(os.getenv("MOOMOO_PORT", 11111))

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
DATA_FOOTER: str = "Data via Yahoo Finance  |  prices may be delayed up to 15 min"
