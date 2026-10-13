# Trading Bot

A modular Discord bot for market data, news intelligence, earnings automation, and
options-flow anomaly detection — with AI analysis via OpenRouter (DeepSeek Flash by default).

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
> `moomoo-api` is only needed for the legacy OpenD backend. The new REST options
> backend uses signed HTTPS requests and does not require OpenD.

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
OPENROUTER_API_KEY=你的OpenRouter Key # 所有 AI 功能；不再直连 DeepSeek
LLM_MODEL=deepseek/deepseek-v4-flash
LLM_TIMEOUT_SECONDS=45
LLM_MAX_TOKENS=2048
LLM_REASONING_ENABLED=false
JIN10_API_KEY=你的Jin10 MCP Token      # Jin10 快讯管线
FRED_API_KEY=你的FRED Key              # !macro 宏观仪表盘 (国债/利差/流动性)
FMP_API_KEY=你的FMP Key                # 财报日历
NEWS_API_KEY=你的NewsAPI Key           # 可选

# --- SEC EDGAR (可选，有默认值；请改成你自己的联系邮箱) ---
SEC_EDGAR_USER_AGENT=YourApp/1.0 (you@example.com)

# --- moomoo 期权异动 (可选；Railway 使用 REST) ---
MOOMOO_APP_KEY=                       # 开通 REST / 期权行情权限后配置
MOOMOO_PRIVATE_KEY_PEM=               # 仅放在本地环境或 Railway Secret
# 期权扫描 / Catalyst 开关、筛选、预算、模型：settings/options.json
```

### 3. 启动机器人
```bash
python bot.py
```
或使用 Docker（见下方 **部署 / Deployment**）。

LLM 不可用时，盘前简报、`!analyze`、新闻摘要和已有基础数据的异动/财报推送
仍显示基础信息，并标注「LLM API 调用失败」。未配置 Key 也走降级。
HTTP 401/402/403 冷却 5 分钟，其他服务错误冷却至少 1 分钟；不自动重试计费请求。
更换模型只需修改 `LLM_MODEL`，但该模型必须支持 JSON 输出用于新闻分类和 SEC 分析。

### 测试与发布 / CI

```bash
python -m unittest discover -s tests -v
```

GitHub Actions 在 push、PR、手动触发和每周一 15:23 UTC 执行离线测试及 Docker 构建。
测试使用临时 SQLite、模拟 HTTP 和模拟 Discord，不访问生产数据库、不发送消息、不消耗 API。
Railway 生产服务保持跟踪 `main`，开启 **Wait for CI**；修复分支通过 PR 合并后再部署。
离线 CI 无法检测真实 API 余额、配额或服务可用性，仍需关注运行日志和数据源告警。
完整配置与故障说明见 [OpenRouter 与 Railway 发布说明](deploy/openrouter-rollout.md)。

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
- LLM 分类失败时以「未AI分类」入摘要队列，不猜测 L3 重要性；摘要失败则发送带原文链接的摘录并标注失败。每轮最多处理 100 条待摘要记录，其余保留到下轮。
- 金十抓取失败和 LLM 失败分开处理；数据源告警发往 `ALERT_CHANNEL_ID`，包含错误类型与最近成功时间，恢复后另发通知。

### 📅 财报工作流 (scheduler.py + earnings_data.py + sec_filings.py)
- **本周财报日历** — 周一 9:00 ET：本周 watchlist 财报安排（FMP + Nasdaq 合并数据源）。
- **盘后财报结果** — 每交易日 17:30 ET：EPS/营收超预期对比 + 盘后股价反应 + DeepSeek 一句话点评。
- **SEC 文件监控** — 每 20 分钟轮询 EDGAR，检测 watchlist 的 8-K (Item 2.02) / 10-Q / 10-K / 6-K；对财报新闻稿 (EX-99.1) 做结构化 AI 解读（关键财务、看涨/看跌、管理层表态、风险），并合并进当日盘后财报推送。

### 🚨 期权异动检测 (options_flow.py + moomoo)
在 [settings/options.json](settings/options.json) 中开启 `moomoo.enabled` 后，默认每 5 分钟做一次无股票白名单的美股期权条件筛选，按 NYSE 实际交易时段运行，支持 DST、节假日和提前收盘。
- 默认 `moomoo.transport=rest`、`scan.universe=market`。只请求 US_STOCK，结果再排除 SPY/QQQ/SPX/IWM/DIA 等宽基指数、ETF 和相关杠杆/反向产品；排除名单在 JSON 中维护，不是对所有 ETF 的自动分类。
- 默认最多 10 页、每页 1,000 条、筛选限时 120 秒。达到上限会报告 partial，不能承诺每轮覆盖全部结果；无法可靠识别的调整合约跳过并计数。OpenD 仅支持显式 watchlist 模式。
- 两阶段处理：成交量 / 金额 / DTE / 流动性过滤，再计算可配置的 0–100 分数。同美东时刻历史基线不足时不加 RVOL 分。
- 同 ticker 合并事件，L3 默认 >=65 分立即推送；L2 默认 >=35 分进入原定时摘要。滚动冷却、失败重试和待发状态存储在 SQLite。
- 可选 Catalyst：本地新闻 / 财报 / SEC -> 有上限的搜索适配 -> DeepSeek 研究 -> Jev Decisions 或 DeepSeek 独立评估。先发量化 embed，再补 AI；AI 失败不阻断推送。
- 每轮最多 4 个即时告警；先全部发送基础信息，再以最多 2 路并发补充 AI。`!optionresearch TICKER` 可用真实 L2+ 候选手动测试流程，不必等待历史数据积累到自动 L3。
- 配置随代码提交部署生效，旧 `OPTIONS_*` / `CATALYST_*` 策略环境变量不再覆盖 JSON。API 密钥和 Discord channel ID 仍放 Railway variables。
- **REST screen 是累计成交数据，不是逐笔成交。** 当日成交额不等于单笔大单；没有证据时买卖方向、重复主动买入、单腿属性均为 unknown。
- `!options` 的 yfinance 期权链保持不变。新增测试自动纳入现有 GitHub Actions。

完整能力核对、字段限制、评分公式和 Railway 启用步骤见 [Options Intelligence V1](deploy/options-intelligence.md)。目前只完成离线验证，未做账户权限 / 实盘行情验证。

---

## 运维命令 / Owner Commands (hidden)

仅机器人 owner 可用，用于手动触发/测试：

| 命令 | 作用 |
|------|------|
| `!premarket` / `!marketsummary` | 立即发送盘前简报 / 市场日报 |
| `!weeklyearnings` / `!todayearnings` | 立即发送本周财报日历 / 今日财报结果 |
| `!secpoll` | 立即轮询 SEC 新文件并分析 |
| `!newsdigest` / `!recentflash [1\|2\|3]` | 立即发送 L2 摘要 / 查看最近分级快讯 |
| `!optionhealth` | 检查当前 REST / OpenD 后端 |
| `!optionscan` | 交易时段立即执行全市场条件筛选（有分页/时间上限） |
| `!optionflow <TICKER>` | 单标的预览，不写库、不发频道通知；闭市时标注旧快照风险 |
| `!optionresearch <TICKER>` | 交易时段的真实 L2+ 候选 + DeepSeek/Jev 预览；需开启 Catalyst，共享每日费用预算 |

---

## 数据来源 / Data Sources

- **股票 / 加密 / 商品 / 期权链**: [Yahoo Finance](https://finance.yahoo.com/) via `yfinance`
- **AI 分析**: [OpenRouter](https://openrouter.ai/) (`deepseek/deepseek-v4-flash`)
- **快讯**: [金十数据 MCP](https://mcp.jin10.com/)
- **宏观**: [FRED](https://fred.stlouisfed.org/) + yfinance
- **财报日历**: [Financial Modeling Prep](https://financialmodelingprep.com/) + [Nasdaq](https://www.nasdaq.com/)
- **公司文件**: [SEC EDGAR](https://www.sec.gov/edgar)
- **期权行情**: [moomoo REST API](https://open.moomoo.com/api/quote/screening/option-screen) / legacy OpenD
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
│   └── llm_client.py        # OpenRouter 统一封装（所有 AI 功能）
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
- **AI 分析**: [OpenRouter](https://openrouter.ai/)
- **快讯数据**: [金十数据 MCP](https://mcp.jin10.com/)

---

## 监控 / Monitoring (Grafana + Prometheus)

OpenRouter 返回的 `usage.cost` 优先计入 `llm_cost_usd_total`；仅在缺失时使用
`LLM_PRICE_*_PER_1M` 估算。指标仍在进程内，重启会归零，不是持久化账单。

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
