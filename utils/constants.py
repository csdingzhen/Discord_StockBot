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

# ---------------------------------------------------------------------------
# Pre-market briefing — tickers
# ---------------------------------------------------------------------------
PREMARKET_TICKERS = {
    "Brent Crude":        "BZ=F",
    "VIX":                "^VIX",
    "Nasdaq Futures":     "NQ=F",
    "S&P 500 Futures":    "ES=F",
    "Gold":               "GC=F",
    "Silver":             "SI=F",
    "US Dollar Index":    "DX-Y.NYB",
    "10Y Treasury Yield": "^TNX",
}

# Pre-market display emojis
PREMARKET_EMOJIS = {
    "Brent Crude":        "🛢️",
    "VIX":                "😨",
    "Nasdaq Futures":     "📊",
    "S&P 500 Futures":    "📊",
    "Gold":               "🥇",
    "Silver":             "🥈",
    "US Dollar Index":    "💵",
    "10Y Treasury Yield": "🏦",
}

# Bilingual (English / Chinese) display labels for the Discord embed
PREMARKET_LABELS_BILINGUAL = {
    "Brent Crude":        "Brent Crude 布伦特原油",
    "VIX":                "VIX 恐慌指数",
    "Nasdaq Futures":     "Nasdaq Futures 纳指期货",
    "S&P 500 Futures":    "S&P 500 Futures 标普期货",
    "Gold":               "Gold 黄金",
    "Silver":             "Silver 白银",
    "US Dollar Index":    "US Dollar Index 美元指数",
    "10Y Treasury Yield": "10Y Treasury Yield 十年期美债收益率",
    "Gold/Oil Ratio":     "Gold/Oil Ratio 金油比",
}

# VIX alert thresholds
VIX_PANIC_THRESHOLD    = 30
VIX_ELEVATED_THRESHOLD = 20

# ---------------------------------------------------------------------------
# Scheduler — market hours
# ---------------------------------------------------------------------------
NORMAL_CLOSE_HOUR = 16  # 4:00 PM ET

# ---------------------------------------------------------------------------
# CNN Fear & Greed index
# ---------------------------------------------------------------------------
CNN_FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_FEAR_GREED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":  "application/json, text/plain, */*",
    "Referer": "https://www.cnn.com/",
    "Origin":  "https://www.cnn.com",
}
