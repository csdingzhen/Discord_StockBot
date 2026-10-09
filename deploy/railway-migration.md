# Railway migration assessment

Reviewed 2026-10-08. This is a migration design, not a completed deployment.
Only the timezone corrections and offline regression tests were implemented.
No production database, Railway settings, or moomoo credentials were changed.

## Recommended architecture

```text
Railway bot (one instance, existing Dockerfile)
  |-- /app/data volume: SQLite state and backups
  |-- private :9091/metrics
  |-- moomoo REST: read-only quotes/options, after validation
  |-- FRED / SEC / other providers where coverage is not equivalent
  |
Railway Alloy collector -- authenticated remote_write --> Grafana Cloud

Alternative: Railway Prometheus + Railway Grafana, each with its own volume.
```

Keep infrastructure changes separate from data-provider replacement. First
migrate the existing bot and its state; then validate moomoo against the old
sources before retiring them. Disable options flow during the interval without
a reachable OpenD or a verified REST replacement.

## SQLite versus PostgreSQL

Keep SQLite initially. `storage/db.py` already stores all application state at
`/app/data/bot.db` inside the Docker image's working directory. The stores use
short transactions and close connections; there is no separate database client
service to migrate. This design fits a single bot process with modest writes.
SQLite permits only one writer at a time; consider PostgreSQL for sustained
write contention or multiple independent services accessing shared state.
[SQLite deployment guidance](https://sqlite.org/whentouse.html).

Attach a Railway volume at **`/app/data`**, not `/app` and not `/data`. No DB
path change is needed. A volume is available at runtime, not during image build
or a pre-deploy command. Do not bake the DB into the image; `.dockerignore`
already excludes `data/`. [Volume mounts](https://docs.railway.com/volumes).

Railway currently prohibits replicas on services with volumes, and volume-backed
redeploys involve a short interruption. That matches the bot's present need for
exactly one scheduler/Discord instance. PostgreSQL would not by itself prevent
duplicate scheduled messages if multiple bot instances were started.
[Volume limitations](https://docs.railway.com/volumes/reference).

This checkout's read-only inspection found a 160 KiB database, `quick_check=ok`,
DELETE journal mode, and only `jin10_flash` and `sec_filings` application tables.
It is not evidence of the current host laptop's full production state. Git
sync does not transfer the ignored database or Docker named volumes.

### Transfer procedure

1. Prepare the Railway service/volume without starting the production bot.
2. Stop the host laptop's supervisor and bot at cutover so there is one writer
   and no competing Discord sender. Obtain the database from that host, not
   automatically from this checkout.
3. Produce a consistent SQLite backup using the backup API or SQLite `.backup`.
   Do not copy only `bot.db` out of a live WAL-mode database: committed data may
   still be in sidecar files. [SQLite backup API](https://sqlite.org/backup.html).
4. Check backup integrity and table row counts. Upload into the selected
   Railway volume as `bot.db` using volume file management, retaining an
   untouched source backup. Do not overwrite a running destination database.
5. Check integrity/row counts on Railway, then start one bot. Confirm all cogs
   loaded and existing seen/digested records survived.
6. Enable scheduled volume backups, test a restore, and retain an independent
   export. Railway explicitly supports backing up SQLite files in volumes.
   [Backups](https://docs.railway.com/volumes/backups).

Consider WAL plus an explicit busy timeout after migration if lock contention
appears, and define retention for option snapshots/news. Neither requires an
immediate database-engine rewrite. Do not share the SQLite file over a network
filesystem with another service. A separate analytics service needing SQL
access would be a stronger reason to adopt PostgreSQL or an aggregation API.

## Monitoring and LLM usage

The synced worktree adds `services/metrics.py`, bot instrumentation, LLM
instrumentation, Prometheus, Grafana provisioning, and node_exporter. The
existing README merge conflict is left unresolved; none of these staged
monitoring changes were reverted.

Railway provides CPU, RAM, disk, and network metrics, but does not automatically
collect application KPIs such as token consumption or LLM latency. Remove the
host node_exporter service and Docker-VM CPU/RAM panels from the cloud setup;
retain application metrics. [Railway metrics](https://docs.railway.com/observability/metrics).

### Preferred: managed dashboard, small collector

Run Grafana Alloy as a separate Railway service. Scrape the bot's private
`<bot-service>.railway.internal:9091/metrics`, then send via authenticated
`remote_write` to Grafana Cloud. Configure the listener for the private network's
address family and verify connectivity from the collector. Persist Alloy's WAL
if buffered telemetry must survive collector redeploys. Import the existing
dashboard JSON and update its datasource UID. Do not expose the bot metrics
endpoint publicly just to make collection easier.
[Alloy remote write](https://grafana.com/docs/grafana-cloud/send-data/alloy/reference/components/prometheus/prometheus.remote_write/),
[private networking](https://docs.railway.com/networking/private-networking).

This avoids operating Grafana and a full historical Prometheus server yourself,
but still has collector resource cost and the managed service's plan limits.
The existing Prometheus service with remote_write is an alternative collector.

### Alternative: everything on Railway

Deploy the official Prometheus and Grafana images as separate services. Put
Prometheus storage at `/prometheus` and Grafana storage at `/var/lib/grafana`
on their respective volumes. Bake provisioning/config into small images rather
than relying on laptop bind mounts. Replace `bot:9091` and `prometheus:9090`
Compose names with the services' Railway private DNS names. Keep Prometheus and
metrics private; expose only authenticated Grafana. Railway supports Docker
images and mapping Compose services to individual services.
[Docker/Compose deployment](https://docs.railway.com/guides/docker-compose).

**Security prerequisite:** the current Compose config enables anonymous Grafana
Admin access and disables basic authentication. Do not publish it unchanged.
Disable anonymous access, require login, and configure credentials through
secret settings. Node-exporter's laptop host mounts are not needed in Railway.

### Make usage summaries useful

Current counters track requests/outcomes, prompt/completion tokens, latency, and
estimated cost by model. The dashboard displays tokens/calls/latency, but has no
cost panel yet. Cost remains zero unless the two `LLM_PRICE_*_PER_1M` settings
are configured: zero here does not mean the provider charged nothing.

Recommended additions, not implemented in this change:

- Add bounded `feature` labels: `premarket`, `news_classify`, `news_digest`,
  `earnings`, `sec_filing`, `options`, `analyze`. Avoid prompts/user IDs as labels.
- Add cost, error ratio, calls, tokens, and latency panels by feature/model.
- Use a suitable rate interval for short-range counter charts. `increase()`
  handles observed counter resets, but scrape gaps and first-sample effects
  mean Prometheus totals are estimates, not an accounting ledger.
- Persist one metadata-only LLM usage record per request in SQLite for durable
  daily/monthly aggregation: UTC timestamp, feature, model, status, latency,
  provider-reported token counts, request ID if available, and pricing version.
  Track cached/uncached input separately where the provider reports it; the
  current two-price formula cannot represent differentiated cache pricing.
- Use decimal/fixed-point values for cost accounting. Expose an owner-only
  summary command or read-only aggregation endpoint; do not mount the bot's
  SQLite volume into a second Grafana service. Reconcile with provider billing.

Migrating `bot.db` does **not** migrate Prometheus history. Old charts live in
the host's `prometheus_data` named volume; exporting dashboard JSON preserves
layout, not historical samples. Archive/export that history separately if needed.

## moomoo: REST, MCP, and skills are distinct

The new API supports direct HTTPS at `https://webapi.moomoo.com`, without OpenD
or its SDK, plus WebSocket quote push. A central client can replace the local
gateway transport. [Overview](https://open.moomoo.com/api/overview/).

Hosted MCP at `https://mcp.moomoo.com/mcp` uses OAuth and is useful for interactive
read-only exploration. Prefer deterministic REST calls inside scheduled bot
jobs: explicit schemas, request budgets, retries, caching, and fixtures are
easier to test than agent-driven tool selection.
[MCP overview](https://open.moomoo.com/mcp-docs/overview).

The Skill Hub's linked installation guide currently distinguishes ready-to-use
search skills from OpenAPI/anomaly skills that **still require OpenD**. Installing
all skills is not the way to remove the gateway. Search/digest/sentiment skills
can inform future features, but scraping/search workflows are not equivalent to
a guaranteed real-time news feed. No skills were installed or executed.
[Skill Hub](https://www.moomoo.com/hans/skillhub),
[linked installation guide](https://www.moomoo.com/skills/moomoo-install.md).

### Authentication

OAuth 2.1 + PKCE is the documented preference: one-time user authorization,
then automated refresh based on token expiry. Use `quote:read` only for this
information bot, not account/trading permissions. The guide says refresh tokens
do not rotate and explicitly advises against storing OAuth tokens in environment
variables. Use encrypted persistent token storage with separately managed key
material, redact logs, and alert on revoked authorization.

The server-side alternative is AppKey plus Ed25519 or RSA-SHA256 signatures,
not a simple API key in the query string. It requires signing the exact final
request and sending timestamp/nonce headers. Default timestamp tolerance is
5 seconds; timezone conversion does not fix clock drift. Neither route has
been authenticated against this account yet.
[Authentication guide](https://open.moomoo.com/api/overview/getting-started).

### Consolidation plan

| Existing feature/source | moomoo candidate | Decision |
|---|---|---|
| yfinance stock/ETF quotes and session prices | Snapshot/stock-quote, historical bars | First consolidation target after symbol, entitlement, freshness, and session validation. |
| OpenD options scanner and yfinance chains | REST option chain + option screener | Replace transport; retain local anomaly scoring and baseline history. |
| Company fundamentals | Financial statements, valuation, analyst research | Can consolidate covered fields; map fiscal periods/currency explicitly. |
| Earnings calendar, actual vs estimate | Financial reports and earnings price-history/move endpoints | Partial coverage demonstrated, not complete calendar/consensus parity; keep FMP/Nasdaq for now. |
| Static index watchlists | Plate/index-related endpoints | Validate exact S&P 500/Dow/Nasdaq-100 constituent coverage before replacing lists. |
| Jin10/NewsAPI | News search/digest skills | Candidate for company-news enrichment; keep Jin10's macro flash pipeline until timeliness/coverage are proven. |
| FRED rates/RRP/TIPS and Stooq JGB yield | Economic calendar/market data | Calendar events do not establish equivalent time series; keep these sources. |
| SEC EDGAR EX-99.1 | Financial statements/research | Keep SEC as the source for the actual filing text and accession identifiers. |
| CNN Fear & Greed | Sentiment/research skills | Different metric; do not silently substitute. |
| Crypto, FX, commodity futures | Per-instrument quote support | Validate each ticker/category; crypto trading support alone does not prove equivalent quote coverage. |

Snapshot has session prices and an options schema, but its supported-market
table lists US equities/ETFs/indices, **not US options**. Stock-quote shows the
same discrepancy. Neither should be assumed to reproduce OpenD's US-option
snapshots without an authenticated probe. Missing requested symbols must not
be treated as successful complete batches. REST snapshot uses `delta` whereas
the current SDK adapter reads `option_delta`.
[Snapshot schema](https://open.moomoo.com/api/quote/realtime/market-snapshot),
[stock-quote schema](https://open.moomoo.com/api/quote/realtime/stock-quote).

The REST option screener explicitly supports US categories and exposes volume,
open interest, IV, Greeks, expiry, and multiplier. Request the needed fields
explicitly; its defaults omit several scanner inputs. It supports delta/DTE
screening, potentially replacing many per-expiry requests. Verify actual
price/ratio scaling against fixtures: documentation mixes scaled filter values
and normalized output examples. This is aggregate options data, not proof of
individual block trades. [Option screener](https://open.moomoo.com/api/quote/screening/option-screen).

Financial statements provide actual reported fields and earnings-price endpoints
provide event-related prices; these pages do not establish a bulk upcoming
weekly calendar or complete EPS/revenue consensus estimates. Validate those
separately instead of removing the current earnings sources based on marketing.
[Statements](https://open.moomoo.com/api/quote/financials/statements),
[earnings prices](https://open.moomoo.com/api/quote/financials/earnings-price-history).

### Verified limits and unknowns

| API | Documented constraint |
|---|---|
| Snapshot | Up to 400 symbols per request. |
| Option chain | At most 20 expiration dates per query; split date windows for more. |
| Option screener | Up to 1,000 results per page; follow opaque pagination cursors. |
| Financial statements | Up to 50 reports per page. |
| REST requests per second/minute/day | No fixed numeric allowance published on the reviewed rate-limit page. |
| WebSocket connections/subscriptions | Account/permission dependent; reviewed guide deliberately gives no fixed number. |

The REST rate guide documents HTTP 429, `Retry-After`, exponential backoff,
batching, and caching. A batch/result cap is **not** a rate allowance. Do not
reuse old OpenD call-rate numbers as REST guarantees. Ask moomoo for this
account's production REST quotas before sizing the full watchlist.
[REST limits](https://open.moomoo.com/api/overview/rate-limit),
[chain limit](https://open.moomoo.com/api/quote/derivatives/option-chain),
[WebSocket quotas](https://open.moomoo.com/api/quote/push/rate-limit).

Implement one shared, configurable rate limiter across commands and scheduled
jobs, request deduplication/cache, bounded retries with jitter, and separate
handling for HTTP errors and nonzero business `ret_code`. Cache chains for the
market day and start with the existing 20-minute options cadence; measure actual
page counts and latency before increasing it. Use WebSocket only when lower
latency is needed and the account's subscription quota is known. Confirm market
data entitlements and permission to redistribute quotes in Discord with moomoo;
an API-access claim does not establish those rights.

## Timezone fix delivered

- `utils/market_time.py` is the shared `America/New_York` clock.
- Scheduler, earnings windows, SEC cutoff dates, options day keys, and option
  expiration horizons use the ET date instead of the host's `date.today()`.
- Existing ET task times are unchanged; news/options/scheduler share one zone.
- Embed timestamps use aware UTC. Stored event timestamps remain UTC.
- Explicit `tzdata` dependency supports environments without system zone data.
- 15 offline tests cover UTC/Los Angeles date boundaries, both DST transitions,
  discord.py next-trigger calculations, holidays/early close, earnings queries,
  SEC filtering, and options cache/digest dates. No Discord messages were sent.

Run from the repository directory:

```powershell
python -m unittest discover -s tests -v
```

These corrections do not add replay of schedules missed during downtime. Run
the existing owner-only triggers after cutover if a scheduled send was missed.
