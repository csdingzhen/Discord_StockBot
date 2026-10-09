"""
premarket_data.py — Pre-market macro indicator fetching for the morning briefing.

Tracks:
  - Brent Crude futures (BZ=F)
  - VIX fear index (^VIX)
  - Nasdaq 100 futures (NQ=F)
  - S&P 500 futures (ES=F)
  - Gold futures (GC=F)
  - Silver futures (SI=F)
  - US Dollar Index (DX-Y.NYB)
  - 10-Year Treasury Yield (^TNX)
  - Derived: Gold / Brent ratio (geopolitical risk gauge)
"""
import yfinance as yf

from services.yf_session import SESSION
from utils.constants import PREMARKET_TICKERS


def _fetch_quote(symbol: str) -> dict:
    info  = yf.Ticker(symbol, session=SESSION).info
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    prev  = info.get("previousClose") or info.get("regularMarketPreviousClose")
    return {"price": price, "prev_close": prev}


def fetch_premarket_snapshot() -> dict:
    """
    Return a dict keyed by label with price data.
    Each entry has: price, prev_close, pct_change (float or None).
    Also includes derived 'Gold/Oil Ratio' entry with a 'value' key.
    """
    snapshot = {}
    for label, sym in PREMARKET_TICKERS.items():
        q = _fetch_quote(sym)
        price, prev = q["price"], q["prev_close"]
        pct = (price - prev) / prev * 100 if price and prev else None
        snapshot[label] = {"price": price, "prev_close": prev, "pct_change": pct}

    gold  = snapshot.get("Gold", {}).get("price")
    brent = snapshot.get("Brent Crude", {}).get("price")
    ratio = round(gold / brent, 2) if gold and brent and brent > 0 else None
    snapshot["Gold/Oil Ratio"] = {"value": ratio}

    return snapshot


def build_data_summary(snapshot: dict) -> str:
    """Plain-text summary of all indicators, used as the LLM prompt context."""
    lines = []
    for label, data in snapshot.items():
        if label == "Gold/Oil Ratio":
            val = data.get("value")
            lines.append(f"Gold/Oil Ratio: {val:.2f}" if val else "Gold/Oil Ratio: N/A")
            continue
        price = data.get("price")
        pct   = data.get("pct_change")
        if price is not None:
            sign    = "+" if (pct or 0) >= 0 else ""
            pct_str = f"{sign}{pct:.2f}%" if pct is not None else "N/A"
            lines.append(f"{label}: {price:,.2f}  ({pct_str})")
        else:
            lines.append(f"{label}: N/A")
    return "\n".join(lines)
