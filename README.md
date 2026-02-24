# Trading Bot

A modular Discord bot for real-time market data, crypto, commodities, options, earnings, macro indicators, and AI-powered technical analysis.

---

## 快速开始 / Quick Start

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置环境变量
在根目录创建 `.env` 文件：
```env
DISCORD_TOKEN=你的Discord Bot Token       # 必填
MARKET_CHANNEL_ID=频道ID                  # 定时推送用，不填则跳过
ALERT_CHANNEL_ID=频道ID                   # 预留警报频道
ANTHROPIC_API_KEY=你的Claude API Key      # !analyze 命令需要
NEWS_API_KEY=你的NewsAPI Key              # 新闻功能需要（可选）
```

### 3. 启动机器人
```bash
python bot.py
```

---

## 命令列表 / Commands

| 命令 | 说明 | 示例 |
|------|------|------|
| `!stock <TICKER>` | 单只股票详细信息 | `!stock AAPL` |
| `!compare <T1> <T2> [T3]` | 对比 2-3 只股票 | `!compare AAPL MSFT GOOG` |
| `!market` | 主要指数快照（S&P 500、道琼斯、纳斯达克、Russell 2000、VIX） | `!market` |
| `!crypto` | 主流加密货币快照 | `!crypto` |
| `!btc` | Bitcoin 快速报价 | `!btc` |
| `!commodities` | 黄金、白银、原油、天然气 | `!commodities` |
| `!options <TICKER>` | 期权链（按未平仓量排序） | `!options SPY` |
| `!earnings <TICKER>` | 财报日历 | `!earnings AAPL` |
| `!macro` | 宏观指标（国债收益率、美元指数等） | `!macro` |
| `!analyze <TICKER>` | AI 技术面分析（需要 Claude API Key） | `!analyze TSLA` |
| `!stockhelp` | 显示所有命令 | `!stockhelp` |

---

## 定时推送 / Scheduler

设置 `.env` 中的 `MARKET_CHANNEL_ID` 后，机器人每 24 小时自动向指定频道推送市场日报。

---

## 项目结构 / Project Spec

```
trading-bot/
├── bot.py                 # 入口，只负责启动和加载cogs
├── config.py              # 所有配置集中管理
├── .env
├── requirements.txt
├── Procfile
│
├── cogs/                  # 每个功能模块独立
│   ├── stocks.py          # 股票和指数基本信息
│   ├── crypto.py          # BTC等虚拟货币
│   ├── commodities.py     # 黄金白银
│   ├── scheduler.py       # 所有定时推送逻辑
│   ├── options.py         # 期权信息
│   ├── earnings.py        # 财报预告和盘后走势
│   ├── macro.py           # 非农、通胀、美联储
│   └── analysis.py        # LLM技术面分析
│
├── services/              # 数据获取层，与discord解耦
│   ├── market_data.py     # yfinance封装
│   ├── news_feed.py       # 新闻API封装
│   ├── options_data.py    # 期权数据封装
│   └── llm_client.py      # Claude/GPT API封装
│
└── utils/
    ├── formatters.py      # embed格式化，颜色逻辑等
    └── constants.py       # 板块龙头股列表等静态数据
```

---

## 数据来源 / Data Sources

- **股票 / 加密 / 期权**: [Yahoo Finance](https://finance.yahoo.com/) via `yfinance`
- **新闻**: [NewsAPI](https://newsapi.org/)
- **AI 分析**: [Anthropic Claude](https://www.anthropic.com/)
