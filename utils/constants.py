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
# NOTE: ^IRX is the 13-Week T-Bill rate, NOT the 2-Year Treasury yield.
#       Use FRED series DGS2 for the actual 2Y yield (see MACRO_DASHBOARD_FRED).
# ---------------------------------------------------------------------------
MACRO_TICKERS = {
    "10Y Treasury Yield": "^TNX",
    "13W T-Bill":         "^IRX",   # was mislabelled "2Y Treasury Yield" — ^IRX is 13-week
    "US Dollar Index":    "DX-Y.NYB",
    "Gold (macro proxy)": "GC=F",
}

# ---------------------------------------------------------------------------
# Macro dashboard — multi-source ticker definitions
# ---------------------------------------------------------------------------

# yfinance tickers
MACRO_DASHBOARD_YFINANCE = {
    "BTC":    "BTC-USD",
    "Gold":   "GC=F",
    "Silver": "SI=F",
    "WTI":    "CL=F",
    "DXY":    "DX-Y.NYB",
    "VIX":    "^VIX",
    "USDCNH": "USDCNH=X",
    "US10Y":  "^TNX",
}

# FRED series (fetched via pandas_datareader)
MACRO_DASHBOARD_FRED = {
    "US2Y":        "DGS2",       # 2-Year Treasury yield (daily)
    "TIPS10Y":     "DFII10",     # 10-Year TIPS real yield (daily)
    "RRP":         "RRPONTSYD",  # Fed overnight reverse repo outstanding ($B, daily)
    "Spread10Y2Y": "T10Y2Y",     # 10Y minus 2Y spread, pre-computed by FRED (daily)
}

# Stooq tickers
MACRO_DASHBOARD_STOOQ = {
    "JP10Y": "10yjpy.b",  # Japan 10-Year Government Bond yield
}

# Bilingual display labels for each macro item key
MACRO_ITEM_LABELS = {
    "BTC":         "比特币 BTC",
    "Gold":        "黄金 Gold",
    "Silver":      "白银 Silver",
    "WTI":         "原油 WTI",
    "GoldSilver":  "金银比 Gold/Silver",
    "GoldOil":     "金油比 Gold/Oil",
    "TIPS10Y":     "10Y TIPS 真实利率",
    "RRP":         "Fed 逆回购 RRP",
    "US10Y":       "美债 10Y",
    "US2Y":        "美债 2Y",
    "JP10Y":       "日债 10Y JGB",
    "USJPSpread":  "美日利差 US-JP",
    "Spread10Y2Y": "10Y-2Y 利差",
    "DXY":         "美元指数 DXY",
    "VIX":         "VIX 恐慌指数",
    "USDCNH":      "USD/CNH 离岸人民币",
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
