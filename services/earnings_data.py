"""
earnings_data.py — Earnings calendar and results via FMP Stable API.

Endpoints used:
  GET /stable/earnings-calendar?from=YYYY-MM-DD&to=YYYY-MM-DD  — date-range calendar
  GET /stable/earnings?symbol=AAPL&limit=N                      — per-ticker history
"""
import requests
from datetime import date, timedelta

from config import FMP_API_KEY

_BASE = "https://financialmodelingprep.com/stable"


def _fmp_get(path: str, params: dict = None) -> list:
    p = dict(params or {})
    p["apikey"] = FMP_API_KEY
    r = requests.get(f"{_BASE}{path}", params=p, timeout=10)
    r.raise_for_status()
    return r.json()


def _week_bounds() -> tuple[str, str]:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


def fetch_weekly_calendar(watchlist: set = None) -> list[dict]:
    """
    Fetch earnings calendar for the current Mon–Fri.
    Filters to watchlist symbols when provided.
    Returns entries sorted by date ascending.
    """
    start, end = _week_bounds()
    try:
        data = _fmp_get("/earnings-calendar", {"from": start, "to": end})
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    if watchlist:
        data = [d for d in data if d.get("symbol") in watchlist]
    data.sort(key=lambda x: x.get("date", ""))
    return data


def fetch_todays_results(watchlist: set = None) -> list[dict]:
    """
    Fetch earnings entries for today that already have actual EPS reported.
    Filters to watchlist symbols when provided.
    """
    today = date.today().isoformat()
    try:
        data = _fmp_get("/earnings-calendar", {"from": today, "to": today})
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    if watchlist:
        data = [d for d in data if d.get("symbol") in watchlist]
    return [d for d in data if d.get("epsActual") is not None]


def fetch_ticker_earnings(ticker: str) -> tuple[dict | None, dict | None]:
    """
    Fetch the next upcoming earnings and the most recent actual report for a ticker.
    Returns (upcoming, most_recent) — each a dict or None.
    """
    try:
        data = _fmp_get("/earnings", {"symbol": ticker.upper(), "limit": 5})
    except Exception:
        return None, None
    if not isinstance(data, list):
        return None, None

    today = date.today().isoformat()
    upcoming = None
    most_recent = None

    for entry in data:
        d = entry.get("date", "")
        has_actual = entry.get("epsActual") is not None
        if d >= today and not has_actual:
            if upcoming is None or d < upcoming["date"]:
                upcoming = entry
        elif d < today and has_actual:
            if most_recent is None or d > most_recent["date"]:
                most_recent = entry

    return upcoming, most_recent
