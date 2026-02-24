# ---------------------------------------------------------------------------
# Major market indices
# ---------------------------------------------------------------------------
INDICES = {
    "S&P 500":          "^GSPC",
    "Dow Jones":        "^DJI",
    "NASDAQ":           "^IXIC",
    "Russell 2000":     "^RUT",
    "VIX (Fear Index)": "^VIX",
}

# ---------------------------------------------------------------------------
# Sector ETFs
# ---------------------------------------------------------------------------
SECTOR_ETFS = {
    "Technology":       "XLK",
    "Healthcare":       "XLV",
    "Financials":       "XLF",
    "Energy":           "XLE",
    "Industrials":      "XLI",
    "Materials":        "XLB",
    "Utilities":        "XLU",
    "Real Estate":      "XLRE",
    "Consumer Disc":    "XLY",
    "Consumer Staples": "XLP",
    "Comm Services":    "XLC",
}

# Sector leaders (top holdings per sector)
SECTOR_LEADERS = {
    "Technology":  ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL"],
    "Healthcare":  ["LLY",  "UNH",  "JNJ",  "ABBV", "MRK"],
    "Financials":  ["BRK-B","JPM",  "V",    "MA",   "BAC"],
    "Energy":      ["XOM",  "CVX",  "COP",  "EOG",  "SLB"],
    "Industrials": ["GE",   "CAT",  "RTX",  "HON",  "UNP"],
}

# ---------------------------------------------------------------------------
# Crypto tickers (via yfinance)
# ---------------------------------------------------------------------------
CRYPTO_TICKERS = {
    "Bitcoin":  "BTC-USD",
    "Ethereum": "ETH-USD",
    "Solana":   "SOL-USD",
    "XRP":      "XRP-USD",
    "BNB":      "BNB-USD",
}

# ---------------------------------------------------------------------------
# Commodity futures
# ---------------------------------------------------------------------------
COMMODITY_TICKERS = {
    "Gold":        "GC=F",
    "Silver":      "SI=F",
    "Oil (WTI)":   "CL=F",
    "Natural Gas": "NG=F",
}

# ---------------------------------------------------------------------------
# Macro proxies available on yfinance
# ---------------------------------------------------------------------------
MACRO_TICKERS = {
    "10Y Treasury Yield": "^TNX",
    "2Y Treasury Yield":  "^IRX",
    "US Dollar Index":    "DX-Y.NYB",
    "Gold (macro proxy)": "GC=F",
}
