# Options Intelligence V1

## Status and scope

Implemented on the `option-flow` branch as a read-only extension of the existing
options cog. No broker orders, new server, queue, database engine, or autonomous
agent framework. No production API calls were made during development. Tests use
synthetic fixtures, not captured production responses.

**Not production-validated:** the account has not enabled the new Moomoo REST
API/US options entitlements. Authentication acceptance, complete OptionItem field
mask, data freshness, account quotas and actual field availability must be checked
after enablement. An offline test passing is not proof of a working live feed.

Existing `!options`/news/earnings/premarket commands are unchanged. Existing owner
commands and the options alert channel remain. Existing OpenD installations can
select `moomoo.transport=opend` with an explicit `scan.watchlist` in the JSON
configuration; the default Railway backend is `rest` with `scan.universe=market`.
The legacy normalizer now also prefers reported turnover and no longer substitutes
100 for an absent contract multiplier. See the
[OpenD snapshot fields](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html).

## Capability audit

Public documentation reviewed on 2026-10-12:

| Requirement | Moomoo REST evidence | V1 behavior |
| --- | --- | --- |
| US options candidate screening | [Option screen](https://open.moomoo.com/zh-cn/api/quote/screening/option-screen) lists US stock options; supports filters, sorting and pagination | No underlying whitelist by default; server-side DTE/volume/turnover gates, then explicit broad-index exclusions |
| Volume, OI, IV, Greeks, strike, expiry, DTE, bid/ask, turnover | Listed screen outputs, subject to selected fields/permissions | Normalize real-unit responses; preserve missing fields |
| Previous OI and opening activity | OI as-of timing is not specified on the screen reference | Report `reported OI`; never infer new opening positions or infinite volume/OI from zero |
| Individual/cluster premium >= $45K | Screen turnover is a daily aggregate, not individual fills | Separate daily candidate gate from optional execution-premium logic |
| Aggressive buys / repeated executions | [REST ticks](https://open.moomoo.com/zh-cn/api/quote/realtime/rt-ticker) and [order book](https://open.moomoo.com/zh-cn/api/quote/realtime/order-book) do not list US options in their supported-products tables | Unknown with this adapter; optional normalized tape logic is unit-tested but not connected |
| Confirmed single-leg trades | [Combo strategies](https://open.moomoo.com/zh-cn/api/quote/derivatives/option-strategy) describe requested combinations, not labels on observed executions | Never label single-leg as confirmed; optional synchronized equal-size fills only flag likely multileg |
| Historical IV / RVOL | [Option volatility](https://open.moomoo.com/zh-cn/api/quote/derivatives/option-volatility) exists; no historical execution tape verified | V1 accumulates its own same-clock snapshot history; that endpoint is not yet integrated |
| Underlying movement / relative volume | Some underlying aggregate fields are listed, but these are not a verified equity RVOL history | Spot used for OTM; no invented underlying-RVOL contribution |
| News / catalyst discovery | [Moomoo news search](https://open.moomoo.com/zh-cn/api/quote/basic-data/search) returns linked results with publication timestamps | Available future adapter; V1 reuses local collectors plus optional OpenRouter search |

WebSocket generic schemas do not, by themselves, establish US-options tape access.
Likewise the legacy OpenD capability/limit tables are not evidence of equivalent
REST permissions. Ask Moomoo to confirm US-options trade/quote support and exchange
timestamps before connecting an execution adapter.

## REST integration and remaining field-mask check

The adapter only permits read-only quote endpoints. Market mode omits indicator
101 (the underlying whitelist) and requests category `0` (US_STOCK) only. Index
category `1` is never requested. Standard equity roots are extracted from returned
contract codes, so tickers outside the old watchlist are discoverable. Unmapped
adjusted roots are skipped rather than guessed, and make coverage partial.

`scan.excluded_underlyings` removes broad-index products after server filtering,
before storage/scoring/AI. It includes SPY, QQQ, SPX/SPXW, IWM, DIA, broad domestic
and international ETFs, and common leveraged/inverse/volatility/covered-call
proxies. This is an explicit maintained list, not a comprehensive security master;
new products may require additions. Sector ETFs are not categorically banned.
Excluded manual previews and previously queued events are blocked as well.
The provider reference does not document a negative-symbol filter, so excluded
rows still consume page capacity; an all-excluded first page does not stop paging.

Single-ticker commands and optional watchlist mode first resolve `US.TICKER` to
`stock_id` using [stock metadata](https://open.moomoo.com/api/quote/basic-data/stock-basicinfo).
Unknown tickers fail closed, never widening a restricted scan. An explicit empty
list is a no-op, distinct from market mode. Company names for AI alerts are fetched
as one small batch after base delivery, not as a prerequisite for the whole market.
Pagination cursors are opaque; reaching `moomoo.max_pages` or
`moomoo.scan_timeout_seconds` reports partial coverage, never a complete market scan.
All totals/concentrations refer to the selected contracts, not all options activity.

[AppKey authentication](https://open.moomoo.com/zh-cn/api/overview/getting-started)
uses Ed25519 or RSA signatures, timestamp and nonce headers. The signature covers
the exact method, path, raw query and body hash. Private PEM material is an
environment secret and is never logged. Redirects are disabled.

The public reference requires **proto OptionItem field names** in `field_filter`,
and gives examples, but does not enumerate that complete proto. In particular,
request names and normalized response names can differ (`hp_strike_price` versus
`strike_price`). The default mask combines documented examples with named metrics;
bid/ask, multiplier, turnover and OI mask acceptance still needs a real account
check. `moomoo.field_filter` can replace the mask with a provider-verified
one. Expiry can be parsed from the contract code; no guessed contract multiplier.
An all-unscorable nonempty response raises an explicit schema error rather than
quietly reporting zero anomalies. Individual missing optional fields remain unknown.

The [REST limits guide](https://open.moomoo.com/api/overview/rate-limit) does not
publish a universal requests/minute quota. Our 1-second spacing, 10-page cap (1,000
rows/page) and 120-second screen deadline are
conservative client settings, **not advertised Moomoo limits**. 429 gets at most
two retries with backoff; a long cooldown fails fast. Authentication/permission
errors are not retried. Account-specific limits require confirmation after access.

## Detection and delivery

1. During the actual NYSE session, collect every `scan.interval_minutes` (default 5).
   Holidays, DST and early closes use the existing market calendar.
2. Filter DTE 0-45, daily volume >=100 and daily premium >=$45,000 by default.
   Reject known stale/future quotes, crossed/zero bids and excessive relative
   spreads. Missing quote timestamps cannot prove freshness; the embed says so.
3. Score contracts, then aggregate by ticker. Score is the strongest contract,
   not a sum that rewards tickers merely for having more strikes.
4. L3 >=65 sends immediately; L2 >=35 goes to the existing four ET digest slots.
   All selected base embeds are sent before any AI call. At most 4 immediate alerts
   per scan, with at most 2 concurrent enrichment jobs, each limited to 60 seconds.
   Digest overflow remains pending. Existing digest slots are 11:30, 13:30, 15:30,
   and 16:15 ET; digests do not call AI.
5. Successful Discord delivery is recorded only after `send` returns. On failure,
   retry the same-day pending event. Delivered events have a rolling 60-minute
   cooldown and require >=100 additional selected-contract volume or +10 score
   to re-alert. Process restart does not reset this state.

Delivery is at-least-once, not exactly-once: a crash between Discord acceptance and
the SQLite commit can duplicate a message. Old-day pending events are retained for
audit but not replayed as current activity. Legacy `option_alerts` rows remain
untouched; old pending rows are not replayed into the new event format.

### Transparent score

The default contributions below are heuristic weights, not fitted probabilities.
Each weight can be changed using `scan.score_weights`; clamp the total to
0-100. All strategy settings are in `settings/options.json`.

| Signal | Default contribution |
| --- | --- |
| Volume > OI | `min(25, round(10 + 10*log2(volume/OI)))`; zero OI gets 10, missing OI gets 0 |
| Daily premium | `min(20, round(5*log2(max(1, premium/min_premium))))` |
| IV expansion | >=5 percentage points versus prior same-clock observation: `min(20, round(2*jump))` |
| Relative volume | At least 5 historical days, ratio >=2: `min(20, round(5*ratio))` |
| Short DTE / OTM | DTE <=14 gets 5; observable OTM adds 5 |
| Interval activity | At least 100 additional contracts since a recent same-day snapshot: 5 |
| Optional tape only | Meaningful quote-inferred buy premium: 10; at least 3 such buys: another 10 |

Historical comparisons use prior days near the **same ET wall-clock minute**,
not an intraday count versus full-day volume. Source backends are isolated; absent
screened contracts are missing, not zero. History is inherently conditional on the
screen filters, so these are not unbiased full-universe RVOL statistics. Newly
listed contracts and cold starts have little history and fewer score contributions.
No comparable-contract percentile or ticker-premium baseline is claimed in V1.

The optional tape input requires stable trade IDs, event times, explicit size and
multiplier. It rejects cancelled/late records, deduplicates IDs and uses only quotes
preceding a trade by <=1 second. Direction is quote inference (80/100 strength),
vendor-only inference (40), mixed or unknown. These numbers are heuristics, not
calibrated probabilities. Likely multileg events lose directional execution bonus
points and get unknown direction. Absence of a matching leg does not prove single-leg.
Repeated-flow scoring currently covers same-contract executions only.

## Catalyst pipeline

- `catalyst.enabled=false` by default; automatic enrichment is for immediate L3
  alerts. Owner-only `!optionresearch TICKER` can research a real L2+ candidate
  during the live session without lowering automatic thresholds. It labels the
  message as a manual preview, does not persist snapshots or affect alert dedup,
  and shares the same concurrency/request/spend guards. No candidate means no AI.
- Local-first retrieval: recent Jin10 text, earnings schedules already fetched by
  existing commands/jobs, SEC filing metadata and timestamped saved SEC analyses.
  Existing SEC summaries without analysis timestamps are not backdated as evidence.
- Jin10 naive publication strings are interpreted as Beijing time; fetched-at is
  a separate UTC observation cutoff. Bounded indexed lookback precedes ticker/alias
  matching. The newest 2,000 local news rows are considered, then at most 10 snippets.
- Company aliases/topics come from profiles, small curated defaults and optional
  `catalyst.company_context`. HUM expands to CMS, Medicare Advantage and Star Ratings.
- Search adapter: `none` or `openrouter`. The latter explicitly invokes the
  [Exa-backed web plugin](https://openrouter.ai/docs/guides/features/plugins/web-search),
  not imagined native DeepSeek browsing. Search only if fewer than two local
  evidence items exist or score >=85, with 1-2 requests and 3-5 results maximum.
- Evidence comes from provider URL annotations with snippets, not generated URLs
  or dates. The annotation schema does not guarantee publication timestamps;
  untimed web results cannot alone yield a `known` classification. Results lacking
  snippets are skipped. Search terms are deterministic; there is no autonomous
  query expansion loop or full-article crawler.
- DeepSeek produces structured research with validated evidence IDs. The judge
  receives original snippets, dates and links plus the proposal, not just the
  DeepSeek conclusion. Research is cached by ticker/context/evidence/model;
  subsequent events reuse research but re-evaluate timing/activity.
- `catalyst.judge_mode=jev` uses the separate
  [OpenRouter Decisions endpoint](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)
  with `typesafe/jev-1.13`, three `noul` questions and one `choice` classification.
  `deepseek` mode uses structured chat judgments for later comparison.
- Evidence Score = 40% support + 35% company materiality + 25% timing. `known`
  requires each >=0.75 and verified publication timestamps for all cited evidence; otherwise cap
  a proposed known catalyst to `possible`/69. `unexplained` is capped at 39, and no
  supporting citations means 0. This is model-assessed evidence, not established
  causation, market-return probability or evidence of insider trading.

Historical mode excludes locally published/observed information after `as_of` and
never searches today's web or uses current research cache. Events older than the
execution window automatically use this mode. Stored future earnings dates may be
used only if the schedule was already observed. The local collector is not a full
point-in-time news archive; the model itself is not guaranteed historically blind.
Historical results should not be represented as a validated no-lookahead backtest.

### Budgets and persistence

- Daily request limit (UTC day) and spend reservations are atomic SQLite records.
  Failed/unknown charges keep a reservation, including after restart. Usage cost,
  token counts, latency, stage, model and event ID are recorded when available.
- `catalyst.daily_budget_usd` is an admission guard using per-call reservations,
  **not a guaranteed billing cap**: actual charges can exceed a reservation.
  Set an OpenRouter key-level credit limit for a provider-enforced limit.
- Input byte cap covers application-supplied prompts/evidence/questions. It does
  not bound provider-injected search context. Chat output uses `max_tokens`;
  Decisions uses a fixed typed question set, not an unsupported `max_tokens` field.
- Search cost is tracked by search-stage usage when reported; missing cost is
  unknown/reserved, not zero. Raw usage metadata is preserved; no invented Exa price.
- Event payloads store component scores, snapshots, concentrations and direction
  evidence. `catalyst_json` stores sources, judgment, models and explanation.
  `record_outcome(event_id, observed_at, values)` is an explicit later-observation
  import hook. Automatic return sampling/backtesting is **not implemented**.
- Raw snapshots retain 45 days by default (minimum 31). Events and usage records
  are retained for evaluation. Monitor volume size; no automatic event deletion.
  Full-market mode can grow the volume much faster than a small watchlist: at the
  10,000-contract page cap, 78 regular-session scans would store about 780,000 rows
  per day before retention. This is an upper-bound example, not measured usage.
  Inspect actual candidate counts/storage, then tighten condition thresholds or
  slow the cadence if needed; do not assume Railway's volume grows without cost.
- Existing metrics endpoint exposes `options_pipeline_total`,
  `options_scan_duration_seconds`, `catalyst_pipeline_total` and shared `llm_*`
  request/token/cost metrics. No new metrics server or Grafana service is required.

## Railway enablement checklist

All nonsecret options settings (including enable flags, backend, model IDs, search
adapter, thresholds, exclusions and budgets) are read from **`settings/options.json`**
relative to the repo, not the shell's working directory. JSON objects replace the
old stringified JSON environment settings. Unknown/missing keys, invalid ranges,
duplicate keys and inconsistent modes fail at startup with a clear configuration
error. Edit locally, run tests, commit, push and redeploy. No extra Railway volume
or dashboard variable is needed; the existing Dockerfile includes this file.

Remove obsolete strategy variables after deploying: all `CATALYST_*`, all
`OPTIONS_*` **except `OPTIONS_ALERT_CHANNEL_ID`**, and all `MOOMOO_*`
**except `MOOMOO_APP_KEY` and `MOOMOO_PRIVATE_KEY_PEM`**. These old strategy variables
are intentionally ignored, not silent overrides. `OPENROUTER_API_KEY`, other API
secrets, Discord token/channel IDs and preexisting non-options deployment settings
remain environment variables. Other features' shared `LLM_*` settings are unchanged;
options research selects `catalyst.research_model` independently.

1. Keep one active bot replica and the existing persistent SQLite volume. Back up
   `bot.db` before deployment; migrations only add tables/columns/indexes.
2. Deploy with `moomoo.enabled=false`, `catalyst.enabled=false` in JSON. Existing features
   should keep working without any Moomoo keys. CI discovers the new tests.
3. After Moomoo access is granted, keep JSON `moomoo.transport=rest`, set `MOOMOO_APP_KEY`,
   `MOOMOO_PRIVATE_KEY_PEM` as Railway variables. PEM can be multiline or use literal
   `\n`. Configure quote-only access; no trading permission is needed by this code.
4. Set the options channel, enable JSON `moomoo.enabled`, redeploy, then run `!optionhealth` and
   `!optionflow AAPL`. Validate required fields, real units, permission scope, field
   mask and freshness against the app. An empty health screen only proves request
   acceptance, not complete data entitlement.
5. During a session use `!optionscan`. Inspect coverage and logs, then allow history
   to accumulate. Closed-session `!optionscan` refuses to persist stale previews;
   `!optionflow` remains a non-persisted preview with a closed-market warning.
6. Verify OpenRouter access to the chosen chat model and Jev; then enable JSON
   `catalyst.enabled`. Search `none` uses only collected local evidence; without
   evidence no research/judge API is called. For full-market trials on companies
   not covered by local news, choose `catalyst.search_provider=openrouter` explicitly
   and keep a provider key spending cap and small daily limits.
7. In the live session, use `!optionresearch AAPL` (or another non-excluded ticker
   with a real L2+ candidate). A cold start has at most 60 aggregate-only score
   points, below the default automatic L3=65; the manual command tests the AI path
   without pretending history exists. Logs report scope, pages, exclusions,
   unresolved roots, event count, completion and collection duration. Per-event
   enrichment duration/cache status and per-stage SQLite usage/cost/latency let you
   measure actual efficiency; offline mocks do not establish real latency or cost.

Do not enable a local copy and Railway copy simultaneously against the same Discord
channels. Rollback switches: disable catalyst enrichment, or disable the scanner;
the rest of the bot is independent. No production settings were changed by this work.

## Offline verification

```powershell
python -m unittest discover -s tests -v
python -m compileall -q services storage cogs config.py
python -m pip check
```

Tests cover signing, pagination/permissions, real units, scoring edge cases, optional
tape classification, same-clock/DST baselines, migrations, dedup/retries, ticker
aggregation, embed limits, local evidence cutoffs, cache, budget concurrency,
search/LLM failure and both judge modes. Live REST, real search charges, Jev model
availability and broker entitlements remain deployment smoke-test requirements.
