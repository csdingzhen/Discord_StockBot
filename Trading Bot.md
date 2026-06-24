---
## 1. Overview
实现一个可以推送美股信息的discord bot，目前运行在本地，之后迁移到服务器上确保7/24稳定运行。

Bot 采用三层架构（入口 → Cogs → Services），模块之间通过明确的接口解耦：Discord 交互逻辑全在 cogs 层，数据获取全在 services 层，格式化工具在 utils 层。用户通过 `!` 前缀命令交互，定时任务通过 `discord.ext.tasks` 在后台按时推送。

---
## 2. Project Spec
status: active
github: https://github.com/csdingzhen/Discord_StockBot
deployment: https://railway.com/dashboard
stack: [Python, Discord API, Railway]


## 3. Goal
- [x] 实现最基础的交互，使用命令让bot发送关于股票和指数的基本信息
- [x] 增加虚拟货币（主要是BTC）的基本信息来了解其对于相关领域的影响
- [x] 增加大宗商品（黄金白银）的基本信息
- [x] 实现交易所日历时间的自动推送的预定基本信息
- [ ] 实现每3小时自动推送影响市场的重大新闻
- [x] 实现股票对应的期权信息推送，包括基本的统计以及最好最差到期日和行使价情况
- [x] 在每周一自动推送本周到会发布财报的重要公司（可以通过设置修改公司名列表）
- [ ] 盘后公司在财报后的股价走势
- [ ] 推送期货数据（原油贵金属等）以及美联储决议，并使用LLM分析
- [ ] 接入LLM帮助分析股票走势（技术面）
- [ ] 实现查看板块龙头行业股价的简略信息（列表）
- [ ] 看板自动推送基本指标（涨跌幅TOP5，反向追涨可能性）；
- [x] 宏观数据仪表盘（汇总核心资产、币价、国债、流动性和风险


## 4. Architecture

### Component Breakdown
```
Discord_Bot/
├── bot.py                  # Entry point — loads all cogs, handles errors
├── config.py               # Centralised env-var loading (DISCORD_TOKEN, API keys, channel IDs)
├── .env                    # Secrets (never committed)
├── requirements.txt        # Pinned runtime dependencies
│
├── cogs/                   # Discord command & task layer
│   ├── stocks.py           # !stock, !compare, !market, !stockhelp
│   ├── crypto.py           # !crypto, !btc
│   ├── commodities.py      # !commodities
│   ├── options.py          # !options
│   ├── earnings.py         # !earnings
│   ├── macro.py            # !macro (宏观仪表盘)
│   ├── analysis.py         # !analyze (LLM AI analysis)
│   └── scheduler.py        # All scheduled push tasks
│
├── services/               # Data-fetching layer — no Discord logic
│   ├── market_data.py      # yfinance wrapper (stocks, indices, crypto, futures)
│   ├── premarket_data.py   # Pre-market snapshot builder
│   ├── macro_data.py       # Multi-source macro fetcher (yfinance + FRED + Stooq)
│   ├── options_data.py     # Options chain via yfinance
│   ├── earnings_data.py    # Earnings calendar + results via FMP API
│   ├── llm_client.py       # Anthropic Claude API wrapper (lazy-initialised)
│   └── news_feed.py        # NewsAPI wrapper (defined but not yet wired to a command)
│
└── utils/
    ├── formatters.py       # Discord embed builders, emoji helpers, number formatters
    └── constants.py        # Static data — index tickers, crypto tickers, macro tickers,
                            #   earnings watchlist, VIX thresholds, CNN F&G URL, etc.
```

### Data Flow
```
User types !<command>
    │
    ▼
Discord API  ──▶  bot.py (routes to correct Cog)
                      │
                      ▼
                  cog method (validates args, calls ctx.typing())
                      │
                      ▼
                  services/ (fetches raw data from external APIs)
                      │
              ┌───────┴──────────────────────┐
          yfinance               FRED REST / Stooq / FMP / CNN
                      │
                      ▼
                  utils/formatters.py  (builds Discord Embed)
                      │
                      ▼
                  channel.send(embed=embed)
```

Scheduled tasks (scheduler.py) bypass user commands and push directly to `MARKET_CHANNEL_ID` on a time-based trigger via `discord.ext.tasks`.

### Integration Points
| External System | Purpose | Auth |
|---|---|---|
| Discord API | Bot gateway, message send/receive | `DISCORD_TOKEN` |
| Yahoo Finance (yfinance) | Stocks, indices, crypto, futures, VIX | None (public) |
| Anthropic Claude API | AI technical analysis + pre-market brief | `ANTHROPIC_API_KEY` |
| FRED REST API | US 2Y yield, TIPS real yield, Fed RRP, yield spread | `FRED_API_KEY` |
| Financial Modeling Prep (FMP) | Earnings calendar, per-ticker earnings history | `FMP_API_KEY` |
| Stooq CSV endpoint | Japan 10Y JGB yield | None (public) |
| CNN Fear & Greed API | Market sentiment index | None (public, User-Agent spoofed) |
| NewsAPI | News headlines (optional feature) | `NEWS_API_KEY` |

---

## 5. Features & Requirements

### 5.1 Core Features

**Interactive Commands**
- Stock lookup with full fundamentals: price, change, volume, 52W range, P/E, market cap, sector
- Side-by-side stock comparison (up to 3 tickers)
- Major index snapshot (S&P 500, Dow, NASDAQ, Russell 2000, VIX) with CNN Fear & Greed
- Crypto snapshot (BTC, ETH, SOL, XRP, BNB) and quick BTC quote
- Commodity prices (Gold, Silver, WTI Oil, Natural Gas)
- Options chain sorted by open interest (nearest expiry, top calls & puts)
- Earnings lookup: upcoming date + EPS/revenue estimate, plus most recent actual vs estimate
- Macro dashboard: core assets, cross-asset ratios (Gold/Silver, Gold/Oil), Fed liquidity (TIPS real yield, RRP), bond yields (US 10Y/2Y, Japan 10Y, yield spreads), risk indicators (DXY, VIX, USD/CNH)
- AI-powered technical analysis via Claude (under 200 words, support/resistance/momentum focus)

**Scheduled Auto-Pushes** (all gated on `MARKET_CHANNEL_ID` being set)
| Task | Time (ET) | Trigger Condition |
|---|---|---|
| Pre-market briefing 盘前简报 | 9:00 AM | NYSE trading day |
| Daily market summary 日报 (normal close) | 4:05 PM | NYSE normal trading day |
| Daily market summary 日报 (early close) | 1:05 PM | NYSE early-close day (e.g. Christmas Eve) |
| Weekly earnings calendar 本周财报日历 | Mon 9:00 AM | NYSE trading day |
| After-market earnings results 盘后财报结果 | 5:30 PM | NYSE trading day, results available |

Pre-market brief includes: futures (NQ, ES), Brent crude, VIX with alert level, Gold, Silver, DXY, 10Y yield, Gold/Oil ratio, and a Claude-generated bilingual macro analysis in Chinese.

### 5.2 Data Sources & APIs

| Source | Library / Method | Data Provided |
|---|---|---|
| Yahoo Finance | `yfinance` | Stock info, crypto, futures, VIX, DXY, US 10Y yield, USD/CNH |
| FRED (St. Louis Fed) | `requests` → REST API | US 2Y yield, 10Y TIPS real yield, Fed overnight RRP, 10Y-2Y spread |
| Stooq | `requests` → CSV download | Japan 10Y JGB yield |
| Financial Modeling Prep | `requests` → REST API | Earnings calendar (date, EPS/revenue estimates & actuals) |
| Anthropic Claude | `anthropic` SDK | Technical analysis text, pre-market macro briefing (Chinese) |
| CNN Fear & Greed | `requests` → JSON endpoint | Sentiment score + rating label |
| NewsAPI | `aiohttp` | News headlines (optional; returns `[]` if key absent) |

---

## 6. Commands

All commands use the prefix `!` (configurable in `config.py`).

| Command | Description | Example |
|---|---|---|
| `!stock <TICKER>` | Full fundamentals for one stock: price, change, day range, 52W range, volume, market cap, P/E, dividend yield, sector/industry | `!stock AAPL` |
| `!compare <T1> <T2> [T3]` | Side-by-side embed for 2–3 stocks | `!compare AAPL MSFT GOOG` |
| `!market` | Live snapshot of S&P 500, Dow Jones, NASDAQ, Russell 2000, VIX | `!market` |
| `!crypto` | Snapshot of BTC, ETH, SOL, XRP, BNB (price + % change) | `!crypto` |
| `!btc` | Quick Bitcoin price quote | `!btc` |
| `!commodities` | Gold, Silver, WTI Oil, Natural Gas futures | `!commodities` |
| `!options <TICKER>` | Top calls & puts by open interest, nearest expiry date | `!options SPY` |
| `!earnings <TICKER>` | Upcoming earnings date + estimates; most recent actual EPS & revenue vs estimate | `!earnings AAPL` |
| `!macro` | Full macro dashboard — core assets, ratios, liquidity, bond yields, risk indicators | `!macro` |
| `!analyze <TICKER>` | Claude AI technical analysis: support/resistance, momentum, short-term outlook | `!analyze TSLA` |
| `!stockhelp` | Lists all available commands with descriptions | `!stockhelp` |

**Owner-only commands** (hidden, for testing scheduled tasks manually):

| Command | Action |
|---|---|
| `!premarket` | Fires the pre-market briefing immediately |
| `!marketsummary` | Fires the daily market summary immediately |
| `!weeklyearnings` | Fires the weekly earnings calendar immediately |
| `!todayearnings` | Fires today's after-market earnings results immediately |

---

## 7. Known Issues & Limitations

- **Invalid Claude model ID**: `llm_client.py` uses `"claude-opus-4-6"` which is not a valid Anthropic model ID. The correct ID is `"claude-opus-4-7"` or `"claude-sonnet-4-6"`. Any `!analyze` or pre-market brief call will return an API error until this is fixed.
- **`news_feed.py` is not wired up**: `services/news_feed.py` is implemented but no cog imports it — there is no `!news` command. The 3-hour auto-push news feature (Goal item) is also not yet implemented.
- **`pandas` missing from `requirements.txt`**: pandas is a transitive dependency (pulled in by `yfinance` and `pandas-market-calendars`) but is not pinned directly. A future yfinance or calendars release could change the pandas version unexpectedly.
- **`aiohttp` pinned to `<3.10`**: `requirements.txt` requires `aiohttp>=3.9.0,<3.10`. Installed version is `3.9.5`. This constraint blocks upgrading to aiohttp 3.10+ which has security and performance improvements.
- **FRED data lag**: FRED series (US 2Y, TIPS, RRP, yield spread) are published daily but can lag 1–2 business days; the macro dashboard may not reflect the very latest values on day-of.
- **yfinance rate limits**: Heavy concurrent use (e.g. `!compare` + `!macro` simultaneously) can trigger Yahoo Finance's informal rate limits and return empty data.
- **No persistent state**: Bot has no database. Watchlists and channel configs are entirely environment-variable based — no per-server customisation.

---

## 8. Reference & Resources

- **GitHub Repo**: https://github.com/csdingzhen/Discord_StockBot
- **Railway Deployment**: https://railway.com/dashboard
- **discord.py Docs**: https://discordpy.readthedocs.io/en/stable/
- **yfinance Docs**: https://ranaroussi.github.io/yfinance/
- **Anthropic API Docs**: https://docs.anthropic.com/
- **FRED API Docs**: https://fred.stlouisfed.org/docs/api/fred/
- **FMP API Docs**: https://site.financialmodelingprep.com/developer/docs/stable
- **CNN Fear & Greed Endpoint**: `https://production.dataviz.cnn.io/index/fearandgreed/graphdata` (unofficial, no auth)
- **Stooq Data**: https://stooq.com (CSV endpoint, no auth required)
- **NewsAPI**: https://newsapi.org/docs
