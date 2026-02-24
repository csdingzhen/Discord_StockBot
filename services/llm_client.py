"""
llm_client.py — Anthropic Claude API wrapper.
Requires ANTHROPIC_API_KEY in .env.
"""
import asyncio
import config

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _call_claude(ticker: str, price_summary: str) -> str:
    """Synchronous Claude call — run via asyncio.to_thread."""
    client = _get_client()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": (
                    f"You are a financial analyst assistant. Provide a brief technical "
                    f"analysis for {ticker} based on the following data:\n\n"
                    f"{price_summary}\n\n"
                    "Keep your response under 200 words. Focus on key support/resistance "
                    "levels, momentum, and a short-term outlook."
                ),
            }
        ],
    )
    return message.content[0].text


async def analyze_ticker(ticker: str, price_summary: str) -> str:
    """
    Async wrapper that runs the blocking Claude call in a thread pool.
    Returns a plain-text analysis string.
    """
    if not config.ANTHROPIC_API_KEY:
        return (
            "LLM analysis is not configured. "
            "Set `ANTHROPIC_API_KEY` in your `.env` file."
        )
    return await asyncio.to_thread(_call_claude, ticker, price_summary)
