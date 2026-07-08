"""
macro_data.py — Macro dashboard data fetching.

Sources:
  - yfinance:                 crypto, commodities, USD index, VIX, USD/CNH, US 10Y
  - FRED (REST API):          US 2Y yield, 10Y TIPS real yield, Fed RRP, 10Y-2Y spread,
                               Japan 10Y govt bond yield (OECD series, monthly not daily)

All public-facing data is returned as plain dicts — no Discord/formatting logic here.

Japan 10Y previously came from Stooq's CSV download, but Stooq now serves a
JavaScript anti-bot challenge instead of CSV on that endpoint (confirmed: a
plain requests.get returns an HTML challenge page even with a browser
User-Agent) — there's no plain-HTTP fix for that, so it's sourced from FRED
instead. The tradeoff is granularity: FRED's Japan series is monthly
(OECD-sourced), not daily like the rest of the bond section.
"""
import requests
import yfinance as yf

from config import FRED_API_KEY
from services.yf_session import SESSION
from utils.constants import (
    MACRO_DASHBOARD_YFINANCE,
    MACRO_DASHBOARD_FRED,
)


# ------------------------------------------------------------------
# Low-level fetchers
# ------------------------------------------------------------------

def _yf_quote(symbol: str) -> dict:
    """
    Return price, pct_change (%), and raw_change (absolute) from yfinance.
    For yield tickers (^TNX, ^VIX) the 'price' field IS the yield value.
    raw_change = price - prev_close  (useful for pp-change display on yields).
    """
    try:
        info  = yf.Ticker(symbol, session=SESSION).info
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev  = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if price is None:
            return {"price": None, "pct_change": None, "raw_change": None}
        pct = (price - prev) / prev * 100 if prev else None
        raw = price - prev if prev is not None else None
        return {"price": price, "pct_change": pct, "raw_change": raw}
    except Exception:
        return {"price": None, "pct_change": None, "raw_change": None}


def _fred_latest(series_id: str) -> dict:
    """
    Return the latest value and absolute 1-period change from a FRED series.
    Uses the FRED REST API (requires FRED_API_KEY in environment).
    Fetches the 5 most recent observations and picks the latest non-missing pair.
    """
    try:
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key":   FRED_API_KEY,
                "sort_order": "desc",
                "limit":      5,
                "file_type":  "json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        obs = [o for o in resp.json()["observations"] if o["value"] != "."]
        if not obs:
            return {"value": None, "change": None}
        value  = float(obs[0]["value"])
        change = value - float(obs[1]["value"]) if len(obs) >= 2 else None
        return {"value": value, "change": change}
    except Exception:
        return {"value": None, "change": None}


# ------------------------------------------------------------------
# Snapshot builder
# ------------------------------------------------------------------

def fetch_macro_snapshot() -> dict:
    """
    Fetch all macro data and return a structured dict grouped by section:
      core_assets, ratios, liquidity, bonds, risk

    Item schemas:
      Price-based (core_assets, risk):  {price, pct_change, raw_change}
      Yield/value-based (bonds, liquidity): {value, change}   change = absolute pp/unit diff
      Ratio (ratios):                   {value}
    """
    # --- yfinance (8 tickers) ---
    yf_data = {key: _yf_quote(sym) for key, sym in MACRO_DASHBOARD_YFINANCE.items()}

    # --- FRED (5 series) ---
    fred_data = {key: _fred_latest(series) for key, series in MACRO_DASHBOARD_FRED.items()}

    # --- Derived: ratios ---
    gold_p   = yf_data.get("Gold",   {}).get("price")
    silver_p = yf_data.get("Silver", {}).get("price")
    oil_p    = yf_data.get("WTI",    {}).get("price")
    gold_silver = round(gold_p / silver_p, 1) if gold_p and silver_p else None
    gold_oil    = round(gold_p / oil_p,    1) if gold_p and oil_p    else None

    # --- Derived: US-Japan yield spread ---
    us10y_val = yf_data.get("US10Y", {}).get("price")   # ^TNX: e.g. 4.35 means 4.35 %
    jp10y_val = fred_data.get("JP10Y", {}).get("value")
    us_jp     = round(us10y_val - jp10y_val, 2) if us10y_val and jp10y_val else None

    # --- Inversion flag for 10Y-2Y spread ---
    spread_val = fred_data.get("Spread10Y2Y", {}).get("value")
    inverted   = spread_val is not None and spread_val < 0

    return {
        "core_assets": {
            "BTC":    yf_data.get("BTC",    {}),
            "Gold":   yf_data.get("Gold",   {}),
            "Silver": yf_data.get("Silver", {}),
            "WTI":    yf_data.get("WTI",    {}),
        },
        "ratios": {
            "GoldSilver": {"value": gold_silver},
            "GoldOil":    {"value": gold_oil},
        },
        "liquidity": {
            "TIPS10Y": fred_data.get("TIPS10Y", {}),
            "RRP":     fred_data.get("RRP",     {}),
        },
        "bonds": {
            # US10Y: from yfinance; use raw_change (pp) for yield-point display
            "US10Y": {
                "value":  us10y_val,
                "change": yf_data.get("US10Y", {}).get("raw_change"),
            },
            "US2Y":        fred_data.get("US2Y", {}),
            "JP10Y":       fred_data.get("JP10Y", {}),
            "USJPSpread":  {"value": us_jp, "change": None},
            "Spread10Y2Y": {**fred_data.get("Spread10Y2Y", {}), "inverted": inverted},
        },
        "risk": {
            "DXY":    yf_data.get("DXY",    {}),
            "VIX":    yf_data.get("VIX",    {}),
            "USDCNH": yf_data.get("USDCNH", {}),
        },
    }
