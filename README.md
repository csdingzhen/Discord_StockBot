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
ALERT_CHANNEL_ID=频道ID                   # Jin10 重大快讯(L3)推送频道
NEWS_CHANNEL_ID=频道ID                    # Jin10 快讯摘要(L2)推送频道
DEEPSEEK_API_KEY=你的DeepSeek API Key     # !analyze、盘前简报、Jin10快讯分析都需要
JIN10_API_KEY=你的Jin10 MCP Token         # Jin10快讯功能需要
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
| `!analyze <TICKER>` | AI 技术面分析（需要 DeepSeek API Key） | `!analyze TSLA` |
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
- **AI 分析**: [DeepSeek](https://www.deepseek.com/)
- **快讯数据**: [金十数据 MCP](https://mcp.jin10.com/)

---

## 监控 / Monitoring (Grafana + Prometheus)

The stack in `docker-compose.yml` runs the bot plus a self-contained
monitoring environment on a shared `monitoring` network:

- **bot** — exposes Prometheus metrics on `:9091` (in-network only) covering
  Discord events/commands, gateway latency, and LLM requests/tokens/latency.
- **prometheus** — scrapes the bot and `node_exporter`; history persists in
  the `prometheus_data` volume.
- **grafana** — dashboards at **http://localhost:3000**.
- **node_exporter** — host/VM CPU, memory, disk.

### Setup

1. Add secrets to `.env` (copy from `.env.example` if you don't have one):

   ```bash
   cp .env.example .env   # then edit in your real keys
   ```

   `.env` is gitignored and injected at runtime — never commit real keys.

2. Build and start everything:

   ```bash
   docker compose up -d --build
   ```

3. Open the UIs:
   - **Grafana** → http://localhost:3000 (anonymous admin, no login) → dashboard
     **“Discord Bot — System, Discord & LLM”** (auto-provisioned).
   - **Prometheus** → http://localhost:9090

### Verify the targets are healthy

In Prometheus, go to **Status → Targets** (or hit the API):

```bash
curl -s http://localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"'
```

All targets (`discord_bot`, `node_exporter`, `prometheus`) should show
`"health":"up"`. In Grafana, the panels start filling within ~15s once scrapes
begin; trigger an `!analyze <TICKER>` to generate LLM/command activity.

> **Note (Windows/Docker Desktop):** `node_exporter` reports the WSL2 Linux VM
> that Docker runs in, not the Windows host directly — which is exactly the
> "is the bot maxing out its container environment?" view.

> **Editing dashboards:** you can edit visually in the Grafana GUI; changes save
> to Grafana's DB (persisted in the `grafana_data` volume). To version-control a
> change, export its JSON (Dashboard **Settings → JSON Model**) and overwrite
> `grafana/provisioning/dashboards/bot-overview.json`.
