# OpenRouter and Railway rollout

## Confirmed diagnosis (2026-10-10)

The user-provided production log contains DeepSeek HTTP 402, "Insufficient
Balance", from news classification. This explains why ordinary classified
news disappeared while Jin10's own roundups still appeared: those roundups
bypass every LLM call.

The old premarket sender awaited the LLM before sending its embed and did
not catch that exception. A failed LLM request could suppress that day's
brief and stop its discord.py loop. This is a verified code path, not proof
of the specific Friday invocation: its own production traceback was not
available. The schedule is 09:00 America/New_York (06:00 Los Angeles), and
starting the bot after that time does not backfill the missed invocation.

The Jin10 outage warning is separate: only failures while connecting to
Jin10 or fetching/parsing pages count toward its five-failure threshold.
It is sent to ALERT_CHANNEL_ID, not NEWS_CHANNEL_ID. Earlier successful
roundups and a later outage warning are therefore compatible. No Jin10
fetch-error log was provided, so its exact production cause is unconfirmed.

## Railway variables

Set these on the bot service before deploying this branch:

```dotenv
OPENROUTER_API_KEY=<your OpenRouter key>
LLM_MODEL=deepseek/deepseek-v4-flash
LLM_TIMEOUT_SECONDS=45
LLM_MAX_TOKENS=2048
LLM_REASONING_ENABLED=false
```

DEEPSEEK_API_KEY and OPENAI_API_KEY are no longer read. Direct DeepSeek
credit does not fund OpenRouter: fund the OpenRouter account/key separately.
Do not commit secrets. Keep JIN10_API_KEY, FRED_API_KEY, FMP_API_KEY and the
other data-source settings: OpenRouter replaces LLM transport, not feeds.

All nine LLM workflows share the OpenRouter client. JSON calls require a
provider that supports response_format. Change LLM_MODEL to another full
provider/model slug without editing callers; validate its JSON capabilities
and output limits before switching. Reasoning is disabled by default for
short summaries/classification: DeepSeek Flash otherwise defaults to high
reasoning, which can consume max_tokens without producing visible output.
For a reasoning-required model, enable it and increase the output/time limits.
Request errors are sanitized in logs.
No automatic retries of potentially billable requests are performed.
Auth/credit/permission errors pause new calls for five minutes; other
failures pause them for at least one minute. Numeric Retry-After can extend
the pause. Missing keys fail immediately into the caller's basic-data path.

## Degraded behavior

Jev is a possible future replacement for the classification/follow-up decision
functions, not for prose summaries. It shares the OpenRouter key/account but
uses POST /api/alpha/decisions (or the System One API), not chat/completions.
It needs its own request/response adapter and confidence-threshold tests;
setting LLM_MODEL to a Jev slug alone is not supported by this chat client.
No Jev integration is enabled in this change.

- Premarket: send available quotes and a failure footer instead of dropping
  the whole embed. Individual missing quotes are shown as unavailable.
- Analyze command: send the fetched quote data and a failure footer.
- Earnings reaction / options interpretation: retain numbers and annotate
  failed optional AI. SEC analyses still require a successful model call;
  this change does not backfill old failed SEC analyses.
- News classification: persist unclassified items in the digest queue, not
  as guessed high-importance alerts. They are not later reclassified as L3.
- News digest: summarize batches of 20, at most 100 per flush. If AI fails,
  send bounded source excerpts with original links and a failure footer.
  Extra backlog stays queued; only successfully delivered pages are marked.
- L3 Discord delivery failures can be retried on later successful polls for
  up to 45 minutes after ingestion; old suppressed records are not replayed.
- Jin10: accept JSON and multiline SSE responses, match JSON-RPC IDs, handle
  structured or text-wrapped tool results, enforce timeouts, and close failed
  initialization sessions. Outage/recovery notices distinguish feed errors
  from model errors without exposing response bodies or credentials.

The Prometheus metric names/labels are unchanged. OpenRouter usage.cost is
preferred over optional configured price estimates. Counters reset on
restart; use Prometheus for historical aggregation, not as an exact ledger.

## CI and release

The Bot CI workflow runs on pushes, PRs, manual dispatch and Mondays at
15:23 UTC. It installs runtime dependencies, runs pip check, executes offline
unittest tests, and builds (but never runs or publishes) the production image.
No production credentials are needed. SQLite integration tests use temporary
databases; HTTP and Discord are mocked. A weekly run detects dependency/build
drift without a new commit, but does not check live vendor uptime or credit.

1. Push the fix branch and open a PR against main.
2. Require Bot CI's test check in GitHub branch protection/rulesets.
3. Keep Railway's production source on main. Enable Wait for CI and accept
   Railway GitHub App permission updates if required.
4. Configure the OpenRouter variables, review the PR, then merge once CI passes.
5. Check every cog's [OK] startup line. Test owner-only !premarket and
   !newsdigest, and !analyze AAPL. These manual checks send real messages and
   can incur API usage; they are deliberately not part of automated CI.
6. Keep the /app/data volume and one active bot instance. No database schema
   migration or old data upload is required by this change.

Production Railway logs, a real OpenRouter completion, and the GitHub-hosted
workflow were not executed from this development session. Retain runtime
monitoring; CI is a regression/build gate, not an external-service monitor.

## References

- [OpenRouter API](https://openrouter.ai/docs/api/reference/overview)
- [OpenRouter error handling](https://openrouter.ai/docs/api/reference/errors-and-debugging)
- [DeepSeek Flash on OpenRouter](https://openrouter.ai/deepseek/deepseek-v4-flash)
- [Reasoning and output budgets](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)
- [Jev on OpenRouter](https://openrouter.ai/docs/guides/community/jev)
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Railway Wait for CI](https://docs.railway.com/deployments/github-autodeploys)
- [GitHub Python CI](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)
