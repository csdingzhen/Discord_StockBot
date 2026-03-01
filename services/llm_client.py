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


def _call_premarket_analysis(data_summary: str) -> str:
    """Synchronous Claude call for pre-market macro briefing — run via asyncio.to_thread."""
    client = _get_client()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": (
                "你是一位专业的盘前宏观分析师。请根据以下盘前指标数据，用中文撰写简报，"
                "重点分析不同品种之间的组合关系及其市场含义。\n\n"
                f"数据：\n{data_summary}\n\n"
                "请分析以下四点（不要用标题，直接分段叙述）：\n"
                "1. 市场模式识别：根据各品种的组合信号，判断当前属于哪种市场环境——"
                "例如「系统性risk-off（油涨+VIX高+纳指跌）」「通胀担忧（油涨+黄金涨+美元强）」"
                "「地缘溢价（金油同涨+美债跌）」「风险偏好回暖（期指涨+VIX低+美元弱）」等。\n"
                "2. 关键跨品种信号：列出2-3个最重要的跨品种关系，说明它们组合在一起意味着什么。\n"
                "3. 板块影响：对科技、能源、防御性板块今日表现的具体含义。\n"
                "4. 今日关键点：一个最值得关注的价位或触发因素。\n\n"
                "要求：不超过280字，简洁直接，可操作性强。"
            ),
        }],
    )
    return message.content[0].text


async def analyze_premarket(data_summary: str) -> str:
    """
    Async wrapper for the pre-market macro briefing Claude call.
    Returns a plain-text analysis string.
    """
    if not config.ANTHROPIC_API_KEY:
        return (
            "LLM analysis is not configured. "
            "Set `ANTHROPIC_API_KEY` in your `.env` file."
        )
    return await asyncio.to_thread(_call_premarket_analysis, data_summary)
