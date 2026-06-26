"""
earnings_data.py — Earnings calendar and results via FMP Stable API + Nasdaq's
unofficial public API.

Endpoints used:
  GET /stable/earnings-calendar?from=YYYY-MM-DD&to=YYYY-MM-DD       — date-range calendar (FMP)
  GET api.nasdaq.com/api/company/{symbol}/earnings-surprise          — per-ticker history (Nasdaq)

FMP's free tier gates the per-symbol /earnings endpoint entirely (HTTP 402 for
many tickers, e.g. AVGO/MU) and also gates the range-calendar endpoint once the
'from' date is more than ~30 days in the past — but forward-looking ranges and
nearby lookback are unrestricted. So upcoming earnings are sourced from FMP's
range endpoint (forward window, never gated), and the most recent actual
report is sourced from Nasdaq's earnings-surprise endpoint instead, which is
free/ungated for any ticker (Nasdaq- or NYSE-listed) but does not carry
revenue figures, only EPS actual/estimate/surprise.
"""
import re
import requests
from datetime import date, datetime, timedelta

from config import FMP_API_KEY

_BASE = "https://financialmodelingprep.com/stable"

_NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


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


def _parse_money_str(s) -> float | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return float(s.replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _fetch_nasdaq_calendar_day(date_str: str) -> list[dict]:
    """One day of Nasdaq's public earnings calendar, normalized to the same
    shape as FMP's calendar entries (symbol/date/epsActual/epsEstimated;
    Nasdaq doesn't carry revenue figures). Used to fill gaps in FMP's free-
    tier calendar dataset, which has been observed missing real entries
    entirely for certain tickers (e.g. MU) even within its ungated window."""
    try:
        r = requests.get(
            "https://api.nasdaq.com/api/calendar/earnings",
            headers=_NASDAQ_HEADERS,
            params={"date": date_str},
            timeout=15,
        )
        r.raise_for_status()
        rows = (r.json().get("data") or {}).get("rows") or []
    except Exception:
        return []

    return [
        {
            "symbol": row.get("symbol"),
            "date": date_str,
            "epsActual": _parse_money_str(row.get("eps")),
            "epsEstimated": _parse_money_str(row.get("epsForecast")),
            "revenueActual": None,
            "revenueEstimated": None,
        }
        for row in rows
        if row.get("symbol")
    ]


def _merge_calendar_sources(fmp_entries: list[dict], nasdaq_entries: list[dict]) -> list[dict]:
    """Union of both sources keyed by (symbol, date), preferring FMP's entry
    when both have one (it carries revenue figures Nasdaq's doesn't)."""
    seen = {(e.get("symbol"), e.get("date")) for e in fmp_entries}
    merged = list(fmp_entries)
    for e in nasdaq_entries:
        key = (e.get("symbol"), e.get("date"))
        if key not in seen:
            merged.append(e)
            seen.add(key)
    return merged


def fetch_weekly_calendar(watchlist: set = None) -> list[dict]:
    """
    Fetch earnings calendar for the current Mon-Fri, merging FMP's bulk range
    endpoint with Nasdaq's per-day calendar to cover entries FMP's free tier
    is missing outright (confirmed gap, not just a 402 gate -- e.g. MU's
    6/24 report never appeared in FMP's range response at all).
    Filters to watchlist symbols when provided.
    Returns entries sorted by date ascending.
    """
    start, end = _week_bounds()
    try:
        fmp_data = _fmp_get("/earnings-calendar", {"from": start, "to": end})
        if not isinstance(fmp_data, list):
            fmp_data = []
    except Exception:
        fmp_data = []

    nasdaq_data = []
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while d <= end_d:
        nasdaq_data.extend(_fetch_nasdaq_calendar_day(d.isoformat()))
        d += timedelta(days=1)

    data = _merge_calendar_sources(fmp_data, nasdaq_data)
    if watchlist:
        data = [d for d in data if d.get("symbol") in watchlist]
    data.sort(key=lambda x: x.get("date", ""))
    return data


def fetch_todays_results(watchlist: set = None) -> list[dict]:
    """
    Fetch earnings entries for today that already have actual EPS reported,
    merging FMP and Nasdaq's calendars for the same gap-filling reason as
    fetch_weekly_calendar(). Filters to watchlist symbols when provided.
    """
    today = date.today().isoformat()
    try:
        fmp_data = _fmp_get("/earnings-calendar", {"from": today, "to": today})
        if not isinstance(fmp_data, list):
            fmp_data = []
    except Exception:
        fmp_data = []

    nasdaq_data = _fetch_nasdaq_calendar_day(today)
    data = _merge_calendar_sources(fmp_data, nasdaq_data)
    if watchlist:
        data = [d for d in data if d.get("symbol") in watchlist]
    return [d for d in data if d.get("epsActual") is not None]


def _fetch_upcoming_from_fmp(ticker: str) -> dict | None:
    """Next upcoming earnings entry for a ticker, via FMP's forward range
    calendar (never gated, unlike the per-symbol endpoint)."""
    today = date.today()
    start = today.isoformat()
    end = (today + timedelta(days=95)).isoformat()
    try:
        data = _fmp_get("/earnings-calendar", {"from": start, "to": end})
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    entries = [e for e in data if e.get("symbol") == ticker and e.get("epsActual") is None]
    if not entries:
        return None
    entries.sort(key=lambda e: e.get("date", ""))
    return entries[0]


def _fetch_upcoming_from_nasdaq(ticker: str) -> dict | None:
    """Next upcoming earnings date for a ticker, via Nasdaq's analyst
    earnings-date endpoint (Zacks-sourced natural-language report, parsed
    with regex). Returns None if Zacks hasn't published a date yet (common
    right after a company just reported — the next quarter's date often
    isn't estimated until 4-6 weeks beforehand)."""
    try:
        r = requests.get(
            f"https://api.nasdaq.com/api/analyst/{ticker}/earnings-date",
            headers=_NASDAQ_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        text = (r.json().get("data") or {}).get("reportText") or ""
    except Exception:
        return None

    if not text or "hasn't provided" in text:
        return None

    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if not date_match:
        return None
    d = datetime.strptime(date_match.group(1), "%m/%d/%Y").date().isoformat()

    eps_match = re.search(r"consensus EPS forecast for the quarter is \$(-?[\d.]+)", text)
    eps_e = float(eps_match.group(1)) if eps_match else None

    timing = None
    if "after market close" in text.lower():
        timing = "amc"
    elif "before market open" in text.lower():
        timing = "bmo"

    return {
        "date": d,
        "epsEstimated": eps_e,
        "revenueEstimated": None,
        "timing": timing,
    }


def _fetch_recent_from_nasdaq(ticker: str) -> dict | None:
    """Most recent actual-vs-estimate report for a ticker, via Nasdaq's public
    earnings-surprise endpoint (no revenue figures, EPS only)."""
    try:
        r = requests.get(
            f"https://api.nasdaq.com/api/company/{ticker}/earnings-surprise",
            headers=_NASDAQ_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    rows = (data.get("data") or {}).get("earningsSurpriseTable", {}).get("rows")
    if not rows:
        return None
    row = rows[0]  # rows are newest-first
    try:
        d = datetime.strptime(row["dateReported"], "%m/%d/%Y").date().isoformat()
    except (KeyError, ValueError):
        d = row.get("dateReported")
    try:
        eps_e = float(row["consensusForecast"])
    except (KeyError, TypeError, ValueError):
        eps_e = None
    return {
        "date": d,
        "epsActual": row.get("eps"),
        "epsEstimated": eps_e,
        "revenueActual": None,
        "revenueEstimated": None,
    }


def fetch_ticker_earnings(ticker: str) -> tuple[dict | None, dict | None, str | None]:
    """
    Fetch the next upcoming earnings and the most recent actual report for a ticker.
    Returns (upcoming, most_recent, error) — error is None on success, or a short
    user-facing reason string.

    Sourced from two free, ungated endpoints rather than FMP's per-symbol
    /earnings endpoint, which the free tier blocks (HTTP 402) for a subset of
    tickers (e.g. AVGO/MU): upcoming comes from FMP's forward-range calendar,
    most-recent-actual comes from Nasdaq's public earnings-surprise endpoint.
    """
    ticker = ticker.upper()
    upcoming = _fetch_upcoming_from_fmp(ticker) or _fetch_upcoming_from_nasdaq(ticker)
    most_recent = _fetch_recent_from_nasdaq(ticker)

    if upcoming is None and most_recent is None:
        return None, None, "暂无财报数据"
    return upcoming, most_recent, None
