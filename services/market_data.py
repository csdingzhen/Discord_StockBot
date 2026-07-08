"""
market_data.py — yfinance wrapper (Discord-decoupled data layer).
"""
import yfinance as yf

from services.yf_session import SESSION


def get_ticker_info(symbol: str) -> dict:
    """Return the yfinance info dict for a symbol."""
    return yf.Ticker(symbol.upper(), session=SESSION).info


def get_history(symbol: str, period: str = "1mo", interval: str = "1d"):
    """Return a DataFrame of OHLCV history."""
    return yf.Ticker(symbol.upper(), session=SESSION).history(period=period, interval=interval)


def get_options_chain(symbol: str, expiry: str = None):
    """
    Return (option_chain, list_of_expiry_dates).
    If expiry is None, uses the nearest available expiry.
    Returns (None, []) if no options exist.
    """
    t = yf.Ticker(symbol.upper(), session=SESSION)
    dates = t.options
    if not dates:
        return None, []
    target = expiry if expiry in dates else dates[0]
    return t.option_chain(target), list(dates)


def get_calendar(symbol: str):
    """Return the yfinance earnings calendar DataFrame (may be None)."""
    return yf.Ticker(symbol.upper(), session=SESSION).calendar
