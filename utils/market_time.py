"""US market wall-clock time, independent of the container's local timezone."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def market_now() -> datetime:
    return datetime.now(ET)


def market_today() -> date:
    return market_now().date()
