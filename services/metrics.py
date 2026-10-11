"""
metrics.py — Prometheus instrumentation shared across the bot.

All metric objects live here (one module → one default registry) so that
bot.py and services/llm_client.py increment the *same* series. The bot calls
start_metrics_server() once at startup, which launches the prometheus_client
HTTP endpoint on its own daemon thread alongside the discord.py event loop.

Scraped by Prometheus at bot:9091 (see prometheus/prometheus.yml).
"""
import os
import math

from prometheus_client import Counter, Gauge, Histogram, start_http_server

METRICS_PORT = int(os.getenv("METRICS_PORT", "9091"))

# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------
discord_events_total = Counter(
    "discord_events_total",
    "Discord events processed, by type (message, command_invoked, "
    "command_completed, command_error, ready).",
    ["type"],
)

discord_command_duration_seconds = Histogram(
    "discord_command_duration_seconds",
    "Wall-clock time spent handling a bot command, by command name.",
    ["command"],
)

discord_api_latency_seconds = Gauge(
    "discord_api_latency_seconds",
    "Discord gateway heartbeat round-trip latency (discord.py bot.latency).",
)

# ---------------------------------------------------------------------------
# LLM (OpenRouter)
# ---------------------------------------------------------------------------
llm_requests_total = Counter(
    "llm_requests_total",
    "LLM API requests, by model and outcome.",
    ["model", "status"],
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "LLM tokens consumed, by model and direction (prompt|completion).",
    ["model", "direction"],
)

llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "LLM API request latency, by model.",
    ["model"],
)

llm_cost_usd_total = Counter(
    "llm_cost_usd_total",
    "LLM spend in USD, by model: provider-reported cost, or a configured estimate.",
    ["model"],
)

# Fallback cost estimation when the provider omits usage.cost. Prices are in
# USD per 1,000,000 tokens, e.g.:
#   LLM_PRICE_PROMPT_PER_1M=0.07
#   LLM_PRICE_COMPLETION_PER_1M=0.27
_PRICE_PROMPT_PER_1M = float(os.getenv("LLM_PRICE_PROMPT_PER_1M", "0") or 0)
_PRICE_COMPLETION_PER_1M = float(os.getenv("LLM_PRICE_COMPLETION_PER_1M", "0") or 0)


def record_llm_cost(model: str, prompt_tokens: int, completion_tokens: int, reported_cost=None) -> None:
    """Prefer OpenRouter's reported cost, including zero; otherwise estimate."""
    if reported_cost is not None:
        try:
            cost = float(reported_cost)
            if math.isfinite(cost) and cost >= 0:
                llm_cost_usd_total.labels(model=model).inc(cost)
                return
        except (ValueError, TypeError):
            pass
    if _PRICE_PROMPT_PER_1M <= 0 and _PRICE_COMPLETION_PER_1M <= 0:
        return
    cost = (
        prompt_tokens / 1_000_000 * _PRICE_PROMPT_PER_1M
        + completion_tokens / 1_000_000 * _PRICE_COMPLETION_PER_1M
    )
    if cost > 0:
        llm_cost_usd_total.labels(model=model).inc(cost)


def start_metrics_server(port: int = METRICS_PORT) -> None:
    """Start the Prometheus metrics HTTP endpoint on a background daemon thread."""
    start_http_server(port)
    print(f"[metrics] Prometheus metrics server listening on :{port}")
