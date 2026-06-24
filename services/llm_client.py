"""
llm_client.py — DeepSeek API wrapper for all LLM-powered features:
ticker technical analysis, pre-market macro briefing, and the Jin10
flash-news pipeline (classification + summarization).
Requires DEEPSEEK_API_KEY in .env.

Targets the v4 model names directly: the legacy `deepseek-chat` /
`deepseek-reasoner` aliases deprecate 2026-07-24.
"""
import json

import aiohttp

import config

_DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
_DEEPSEEK_MODEL = "deepseek-v4-flash"

_NOT_CONFIGURED_MSG = (
    "LLM analysis is not configured. Set `DEEPSEEK_API_KEY` in your `.env` file."
)


async def _call_deepseek(messages: list[dict], response_format: dict | None = None) -> str:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured in .env")
    payload = {"model": _DEEPSEEK_MODEL, "messages": messages}
    if response_format:
        payload["response_format"] = response_format
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(_DEEPSEEK_URL, headers=headers, json=payload) as resp:
            body = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"DeepSeek API error {resp.status}: {body}")
            return body["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Ticker technical analysis — !analyze
# ---------------------------------------------------------------------------

async def analyze_ticker(ticker: str, price_summary: str) -> str:
    """Returns a plain-text technical analysis string for one ticker."""
    if not config.DEEPSEEK_API_KEY:
        return _NOT_CONFIGURED_MSG
    prompt = (
        f"You are a financial analyst assistant. Provide a brief technical "
        f"analysis for {ticker} based on the following data:\n\n"
        f"{price_summary}\n\n"
        "Keep your response under 200 words. Focus on key support/resistance "
        "levels, momentum, and a short-term outlook."
    )
    return await _call_deepseek([{"role": "user", "content": prompt}])


# ---------------------------------------------------------------------------
# Pre-market macro briefing
# ---------------------------------------------------------------------------

async def analyze_premarket(data_summary: str) -> str:
    """Returns a plain-text Chinese pre-market macro briefing string."""
    if not config.DEEPSEEK_API_KEY:
        return _NOT_CONFIGURED_MSG
    prompt = (
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
    )
    return await _call_deepseek([{"role": "user", "content": prompt}])


# ---------------------------------------------------------------------------
# Jin10 flash-news classification & summarization
# ---------------------------------------------------------------------------

_FLASH_CATEGORIES = [
    "美联储", "通胀数据", "非农", "利率", "地缘政治", "中国监管",
    "半导体", "AI", "财报", "能源", "外汇", "大宗商品", "其他",
]


async def classify_flash(content: str) -> dict:
    """
    Classify one Jin10 flash item. Returns {'level': 1-3, 'category': str}.

    1 (低) — 普通市场评论、个股小幅波动、零散行情播报，与宏观/重大事件无关。
    2 (中) — 有参考价值的市场/行业/政策新闻，值得记录但不需要即时提醒。
    3 (高) — 重大宏观数据、央行决议、地缘政治突发事件、重大监管或政策变化，
             可能引发市场大幅波动的突发新闻。
    """
    prompt = (
        "你是一名金融快讯重要性分类助手。请阅读以下金十快讯内容，判断其重要等级和分类标签，"
        "只返回JSON，不要任何额外文字。着重注意美股，美股科技公司的新闻是中等重要性，其他市场如果没有大幅波动或者熔断则设为低重要性\n\n"
        "等级定义：\n"
        "1 (低) - 普通市场评论、除美股科技公司外的个股小幅波动、零散行情播报、与宏观/重大事件无关的内容。\n"
        "2 (中) - 有参考价值的市场/行业/政策新闻，值得记录但不需要即时提醒。\n"
        "3 (高) - 特朗普相关的新闻，重大宏观数据(CPI/PPI/非农/PMI)、央行决议、地缘政治突发事件、重大监管或政策变化、"
        "可能引发市场大幅波动的突发新闻。\n\n"
        f"分类标签从以下选择: {', '.join(_FLASH_CATEGORIES)}\n\n"
        f"快讯内容: {content}\n\n"
        '请返回JSON: {"level": 1到3的整数, "category": "分类标签"}'
    )
    raw = await _call_deepseek(
        [{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    data = json.loads(raw)
    level = int(data.get("level", 1))
    if level not in (1, 2, 3):
        level = 1
    category = data.get("category") or "其他"
    return {"level": level, "category": category}


async def summarize_flash_alert(content: str) -> str:
    """L3 — full AI analysis for one high-importance flash item, max 5 bullets, Chinese."""
    prompt = (
        "请根据以下金十快讯内容，用中文生成不超过5条要点的分析，包括：事件摘要、市场影响、"
        "可能受影响的资产、风险偏好(risk-on/risk-off)。不要提供投资建议，只提供信息、风险和背景。\n\n"
        f"快讯内容: {content}\n\n"
        "请用简洁的中文bullet point列出，每条不超过30字，不超过5条。"
    )
    return await _call_deepseek([{"role": "user", "content": prompt}])


async def summarize_flash_digest(items: list[dict]) -> str:
    """L2 — one condensed roll-up summarizing a batch of moderate-importance flash items."""
    listing = "\n".join(f"{i + 1}. [{it['time']}] {it['content']}" for i, it in enumerate(items))
    prompt = (
        f"以下是过去一段时间内的{len(items)}条金十快讯，请按主题归类，用中文生成一份简洁摘要，"
        "不超过6条要点，每条不超过30字，标注涉及的关键资产/行业/地区。不要提供投资建议。\n\n"
        f"快讯列表:\n{listing}"
    )
    return await _call_deepseek([{"role": "user", "content": prompt}])
