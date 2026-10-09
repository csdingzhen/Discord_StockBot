# Trading Bot

A modular Discord bot for market data, news intelligence, earnings automation, and
options-flow anomaly detection — with AI analysis powered by DeepSeek.

Beyond on-demand commands, the bot runs several **automated pipelines**: Jin10 flash-news
classification and delivery, a weekly + after-market earnings workflow (including SEC
filing analysis), a pre-market macro brief, and moomoo-based unusual-options-activity
alerts.

---

## 快速开始 / Quick Start

### 1. 安装依赖
```bash
pip install -r requirements.txt
```
> `moomoo-api` is only needed for the options-flow feature. The rest of the bot runs
> without it (the module stays dormant unless `MOOMOO_ENABLED=true`).

### 2. 配置环境变量
在根目录创建 `.env` 文件：

```env
# --- Discord (必填) ---
DISCORD_TOKEN=你的Discord Bot Token

# --- 推送频道 ID (不填则对应功能跳过) ---
MARKET_CHANNEL_ID=频道ID              # 盘前简报 / 市场日报 / 财报推送
ALERT_CHANNEL_ID=频道ID               # Jin10 重大快讯(L3)；期权异动默认频道
NEWS_CHANNEL_ID=频道ID                # Jin10 快讯摘要(L2)
OPTIONS_ALERT_CHANNEL_ID=频道ID       # 期权异动(可选，不填则用 ALERT_CHANNEL_ID)

# --- AI / 数据源 Key ---
DEEPSEEK_API_KEY=你的DeepSeek Key      # 所有 AI 功能：!analyze、盘前简报、快讯/财报/期权分析
JIN10_API_KEY=你的Jin10 MCP Token      # Jin10 快讯管线
FRED_API_KEY=你的FRED Key              # !macro 宏观仪表盘 (国债/利差/流动性)
FMP_API_KEY=你的FMP Key                # 财报日历
NEWS_API_KEY=你的NewsAPI Key           # 可选

# --- SEC EDGAR (可选，有默认值；请改成你自己的联系邮箱) ---
SEC_EDGAR_USER_AGENT=YourApp/1.0 (you@example.com)

# --- moomoo 期权异动 (可选，需本地 OpenD) ---
MOOMOO_ENABLED=false                  # 设为 true 开启期权异动扫描
MOOMOO_HOST=127.0.0.1                 # Docker 内改为 host.docker.internal
MOOMOO_PORT=11111
```

### 3. 启动机器人
```bash
python bot.py
```
或使用 Docker（见下方 **部署 / Deployment**）。

---

## 交互命令 / Interactive Commands

| 命令 | 说明 | 示例 |
|------|------|------|
| `!stock <TICKER>` | 单只股票详细信息 | `!stock AAPL` |
| `!compare <T1> <T2> [T3]` | 对比 2-3 只股票 | `!compare AAPL MSFT GOOG` |
| `!market` | 主要指数快照（标普/道指/纳指/Russell 2000/VIX） | `!market` |
| `!crypto` | 主流加密货币快照 | `!crypto` |
| `!btc` | Bitcoin 快速报价 | `!btc` |
| `!commodities` | 黄金、白银、原油、天然气 | `!commodities` |
| `!options <TICKER>` | 期权链快照（按未平仓量排序，yfinance） | `!options SPY` |
| `!earnings <TICKER>` | 下次财报预期 + 最近财报实际对比 | `!earnings AAPL` |
| `!macro` | 宏观仪表盘（核心资产、比率、流动性、利率、风险） | `!macro` |
| `!analyze <TICKER>` | AI 技术面分析 | `!analyze TSLA` |
| `!stockhelp` | 显示所有命令 | `!stockhelp` |

---

## 自动化管线 / Automated Pipelines

所有定时任务按 **美东时间 (ET)** 触发，并跳过 NYSE 假日/周末。

### 📊 盘前 & 收盘 (scheduler.py)
- **盘前简报** — 交易日 9:00 ET：盘前指标（原油、VIX、期指、黄金、美元、美债）+ DeepSeek 跨品种宏观解读。
- **市场日报** — 收盘后 16:05 ET（提前收市日 13:05）：主要指数 + CNN 恐慌贪婪指数。

### 📰 Jin10 快讯情报 (news.py)
持续轮询金十 MCP，AI 分级投递：
- **L1（低）** — 仅入库，不推送。
- **L2（中）** — 汇总为定时摘要（盘前 / 盘中每小时 / 盘后）推送到 `NEWS_CHANNEL_ID`。
- **L3（高）** — 立即推送带 AI 解读的重大快讯到 `ALERT_CHANNEL_ID`，并做重复事件抑制。
- 金十自身的「速递/要闻」类汇总帖直接原样转发；轮询连续失败会告警。

### 📅 财报工作流 (scheduler.py + earnings_data.py + sec_filings.py)
- **本周财报日历** — 周一 9:00 ET：本周 watchlist 财报安排（FMP + Nasdaq 合并数据源）。
- **盘后财报结果** — 每交易日 17:30 ET：EPS/营收超预期对比 + 盘后股价反应 + DeepSeek 一句话点评。
- **SEC 文件监控** — 每 20 分钟轮询 EDGAR，检测 watchlist 的 8-K (Item 2.02) / 10-Q / 10-K / 6-K；对财报新闻稿 (EX-99.1) 做结构化 AI 解读（关键财务、看涨/看跌、管理层表态、风险），并合并进当日盘后财报推送。

### 🚨 期权异动检测 (options_flow.py + moomoo)
需本地 moomoo OpenD 且 `MOOMOO_ENABLED=true`。交易时段每 20 分钟扫描 `OPTIONS_WATCHLIST`：
- 每日构建近月合约universe，盘中批量拉取 `get_market_snapshot`（成交量/持仓量/IV/Delta）。
- **判定逻辑**：必要门槛「当日成交量 > 持仓量」，叠加佐证信号（量仓比、名义成交额、IV 跳升、近月平值、成交量 z-score）。
- **L3** = 门槛 + ≥1 佐证（或极端名义额/z-score）→ 立即带 AI 解读推送；**L2** → 汇总摘要；单只标的 L3 数量设上限防刷屏。
- ⚠️ 基于**聚合快照**（非逐笔成交）；「名义成交额」为 量×价×乘数 的估算，非真实大单数据。

---

## 运维命令 / Owner Commands (hidden)

仅机器人 owner 可用，用于手动触发/测试：

| 命令 | 作用 |
|------|------|
| `!premarket` / `!marketsummary` | 立即发送盘前简报 / 市场日报 |
| `!weeklyearnings` / `!todayearnings` | 立即发送本周财报日历 / 今日财报结果 |
| `!secpoll` | 立即轮询 SEC 新文件并分析 |
| `!newsdigest` / `!recentflash [1\|2\|3]` | 立即发送 L2 摘要 / 查看最近分级快讯 |
| `!optionhealth` | 检查 moomoo OpenD 连接 |
| `!optionscan` | 立即执行一次全 watchlist 期权扫描 |
| `!optionflow <TICKER>` | 按需扫描单只标的期权异动 |

---

## 数据来源 / Data Sources

- **股票 / 加密 / 商品 / 期权链**: [Yahoo Finance](https://finance.yahoo.com/) via `yfinance`
- **AI 分析**: [DeepSeek](https://www.deepseek.com/) (`deepseek-v4-flash`)
- **快讯**: [金十数据 MCP](https://mcp.jin10.com/)
- **宏观**: [FRED](https://fred.stlouisfed.org/) + yfinance
- **财报日历**: [Financial Modeling Prep](https://financialmodelingprep.com/) + [Nasdaq](https://www.nasdaq.com/)
- **公司文件**: [SEC EDGAR](https://www.sec.gov/edgar)
- **期权行情**: [moomoo OpenAPI](https://openapi.moomoo.com/) (本地 OpenD)
- **恐慌贪婪指数**: CNN

---

## 项目结构 / Project Structure

```
Discord_StockBot/
├── bot.py                   # 入口：加载 cogs、启动
├── config.py                # 所有环境变量集中管理
├── requirements.txt
├── Dockerfile / docker-compose.yml
│
├── cogs/                    # Discord 层（命令 + 定时任务）
│   ├── stocks.py            # !stock !compare !market !stockhelp
│   ├── crypto.py            # !crypto !btc
│   ├── commodities.py       # !commodities
│   ├── options.py           # !options（yfinance 期权链快照）
│   ├── options_flow.py      # 期权异动扫描管线（moomoo）
│   ├── earnings.py          # !earnings
│   ├── macro.py             # !macro 宏观仪表盘
│   ├── analysis.py          # !analyze
│   ├── scheduler.py         # 盘前/收盘/财报/SEC 定时任务
│   └── news.py              # Jin10 快讯管线
│
├── services/                # 数据层（与 Discord 解耦）
│   ├── market_data.py       # yfinance 封装
│   ├── premarket_data.py    # 盘前指标聚合
│   ├── macro_data.py        # FRED + yfinance 宏观数据
│   ├── earnings_data.py     # FMP + Nasdaq 财报数据
│   ├── sec_filings.py       # SEC EDGAR 文件监控/抓取
│   ├── options_data.py      # yfinance 期权链
│   ├── options_scan.py      # 期权异动评分/分级（纯逻辑）
│   ├── moomoo_client.py     # moomoo SDK 封装（同步，走 OpenD）
│   ├── jin10_mcp.py         # 金十 MCP 客户端
│   ├── news_feed.py         # NewsAPI 封装
│   └── llm_client.py        # DeepSeek API 封装（所有 AI 功能）
│
├── storage/                 # SQLite 持久化 (data/bot.db)
│   ├── db.py                # 连接助手
│   ├── jin10_store.py       # 快讯去重/分级/摘要状态
│   ├── sec_store.py         # SEC 文件去重/分析缓存
│   └── options_store.py     # 期权快照历史 + 异动去重/基线
│
└── utils/
    ├── formatters.py        # embed 格式化、颜色逻辑
    └── constants.py         # watchlist、阈值等静态配置
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
