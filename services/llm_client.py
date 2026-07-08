"""
llm_client.py — DeepSeek API wrapper for all LLM-powered features:
ticker technical analysis, pre-market macro briefing, and the Jin10
flash-news pipeline (classification + summarization).
Requires DEEPSEEK_API_KEY in .env.

Targets the v4 model names directly: the legacy `deepseek-chat` /
`deepseek-reasoner` aliases deprecate 2026-07-24.
"""
import json
import time

import aiohttp

import config
from services.metrics import (
    llm_request_duration_seconds,
    llm_requests_total,
    llm_tokens_total,
    record_llm_cost,
)

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
    # Instrumentation: status defaults to "error" and is flipped to "success"
    # only on a clean 200 return, so any exception path (network, non-200,
    # JSON/parse) is counted as an error exactly once by the finally block.
    start = time.perf_counter()
    status = "error"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(_DEEPSEEK_URL, headers=headers, json=payload) as resp:
                body = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"DeepSeek API error {resp.status}: {body}")
                usage = body.get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                if prompt_tokens:
                    llm_tokens_total.labels(model=_DEEPSEEK_MODEL, direction="prompt").inc(prompt_tokens)
                if completion_tokens:
                    llm_tokens_total.labels(model=_DEEPSEEK_MODEL, direction="completion").inc(completion_tokens)
                record_llm_cost(_DEEPSEEK_MODEL, prompt_tokens, completion_tokens)
                status = "success"
                return body["choices"][0]["message"]["content"]
    finally:
        llm_request_duration_seconds.labels(model=_DEEPSEEK_MODEL).observe(time.perf_counter() - start)
        llm_requests_total.labels(model=_DEEPSEEK_MODEL, status=status).inc()


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
        "只返回JSON，不要任何额外文字。\n\n"
        "判断原则：重要等级应基于这条信息是否会实质性改变市场对未来的预期，而不是仅仅因为话题敏感"
        "（如战争、地缘政治、知名政治人物言论）就直接判定为高等级。同一事件持续发展过程中的常规进展，"
        "如果没有带来新的实质信息，不应重复给高等级。\n\n"
        "等级定义：\n"
        "1 (低) - 常规市场评论、个股小幅波动、零散行情播报；非美股市场若没有出现大幅波动或熔断；"
        "持续进行中的冲突/战争的常规战报（如每日伤亡或装备损失数字、日常交火，没有新的升级信号）；"
        "政治人物的言论/表态/社交媒体发帖但未伴随实际政策行动；与宏观/重大事件无关的内容。\n"
        "2 (中) - 有参考价值但非突发的市场/行业/政策新闻；美股科技公司相关新闻；地缘冲突中出现新进展"
        "但尚不构成重大升级；政治人物表态涉及具体政策方向但尚未正式落地执行。\n"
        "3 (高) - 仅当满足以下情况之一时才使用：\n"
        "  - 重大宏观数据明显超出或低于市场预期(CPI/PPI/非农/PMI等)\n"
        "  - 央行意外决议或政策转向\n"
        "  - 任何国家的股市熔断或大幅下跌（如单日跌幅超过5%）\n"
        "  - 地缘冲突出现重大升级信号（如核设施/能源基础设施被直接打击、第三方大国直接军事介入、"
        "大规模平民伤亡引发国际制裁或军事响应、停火协议达成或破裂）\n"
        "  - 已正式宣布或生效的重大监管/政策行动（不是威胁或表态，而是实际行动，如关税正式生效、"
        "制裁正式宣布、央行人事变动落地）\n"
        "  - 其他可能立即引发市场大幅波动的突发新闻\n\n"
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


async def is_followup_story(new_content: str, recent_contents: list[str]) -> bool:
    """
    Check whether new_content is a follow-up/restatement of the same
    unfolding event as any of recent_contents (e.g. a developing story
    getting several near-duplicate flash updates in a row), rather than a
    genuinely distinct new event. Used to avoid posting multiple L3 alerts
    about one story as it develops.
    """
    if not recent_contents:
        return False
    listing = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(recent_contents))
    prompt = (
        "判断下面的「新快讯」是否属于同一事件的后续/重复报道——例如同一突发新闻的连续跟进、"
        "同一人物针对同一事件的多条相关表态、同一事件的滚动更新——而不是一个独立的新事件。\n\n"
        f"最近已推送的重大快讯：\n{listing}\n\n"
        f"新快讯：{new_content}\n\n"
        '只返回JSON：{"is_followup": true 或 false}'
    )
    raw = await _call_deepseek(
        [{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    data = json.loads(raw)
    return bool(data.get("is_followup", False))


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


async def analyze_earnings_reaction(
    symbol: str,
    eps_actual, eps_estimated,
    revenue_actual, revenue_estimated,
    postmarket_pct: float,
) -> str:
    """
    One-line Chinese narrative tying EPS/revenue beat-or-miss together with
    the after-hours price reaction — e.g. flagging a beat that still sold
    off (often a guidance signal) rather than just restating the numbers.
    """
    prompt = (
        f"以下是{symbol}的财报数据：\n"
        f"EPS: 实际 {eps_actual}，预期 {eps_estimated}\n"
        f"营收: 实际 {revenue_actual}，预期 {revenue_estimated}\n"
        f"盘后股价变动: {postmarket_pct:+.2f}%\n\n"
        "请用一句话（不超过40字）中文点评财报数字与盘后股价反应是否一致。如果不一致"
        "（例如业绩超预期但股价下跌），简要推测可能原因（如指引不及预期、估值已price-in等）。"
        "不要提供投资建议。"
    )
    return await _call_deepseek([{"role": "user", "content": prompt}])


# ---------------------------------------------------------------------------
# SEC EDGAR filing analysis (8-K / EX-99.1 earnings press release)
# ---------------------------------------------------------------------------

_MAX_FILING_TEXT_CHARS = 40_000


async def analyze_sec_filing(ticker: str, form_type: str, text: str) -> dict:
    """
    Structured Chinese analysis of an SEC earnings filing (typically an
    EX-99.1 press release attached to an 8-K). Returns a dict with:
    executive_summary, key_financials (list), bullish_highlights (list),
    bearish_highlights (list), management_commentary (list), risks (list),
    market_implications (str).

    key_financials/management_commentary are meant to be facts extracted
    directly from the filing text; bullish/bearish/risks/market_implications
    are explicitly AI interpretation built on top of those facts — the
    prompt keeps that distinction so the Discord output doesn't blur
    "what the filing says" with "what the model thinks it means." Any
    metric not present in the text must be omitted, never invented.
    """
    truncated = text[:_MAX_FILING_TEXT_CHARS]
    prompt = (
        f"以下是{ticker}的SEC文件（{form_type}）内容，通常是财报新闻稿。"
        "请仔细阅读并提取对交易者有用的信息，只返回JSON，不要任何额外文字。\n\n"
        "重要原则：\n"
        "- key_financials 和 management_commentary 必须是从原文直接提取的事实"
        "（数字、原话），不要加入解读。\n"
        "- bullish_highlights、bearish_highlights、risks、market_implications 是你"
        "基于上述事实做出的解读和推断，不是原文直接陈述。\n"
        "- 绝对不要编造原文中没有的数据。如果某项指标原文未提及，直接省略，不要猜测或标注N/A填充。\n"
        "- 不要提供投资建议。\n\n"
        f"文件内容：\n{truncated}\n\n"
        "请返回JSON，格式如下：\n"
        '{"executive_summary": "一段话概述（中文，不超过100字）", '
        '"key_financials": ["从原文提取的关键财务数据，每条一个指标"], '
        '"bullish_highlights": ["看涨要点"], '
        '"bearish_highlights": ["看跌要点"], '
        '"management_commentary": ["管理层重要表态，原文引用或紧贴原意"], '
        '"risks": ["潜在风险"], '
        '"market_implications": "对市场/股价的可能影响（一句话，不超过60字）"}'
    )
    raw = await _call_deepseek(
        [{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    data = json.loads(raw)
    return {
        "executive_summary": data.get("executive_summary") or "",
        "key_financials": data.get("key_financials") or [],
        "bullish_highlights": data.get("bullish_highlights") or [],
        "bearish_highlights": data.get("bearish_highlights") or [],
        "management_commentary": data.get("management_commentary") or [],
        "risks": data.get("risks") or [],
        "market_implications": data.get("market_implications") or "",
    }


# ---------------------------------------------------------------------------
# Options-flow anomaly interpretation
# ---------------------------------------------------------------------------

async def analyze_options_anomaly(
    contract_label: str,
    option_type: str,
    signals: list[str],
    metrics: dict,
    underlying_price: float | None,
    earnings_date: str | None = None,
) -> str:
    """
    Short Chinese interpretation of an unusual-options-activity alert: likely
    directional lean (bullish/bearish), whether it reads as speculative vs
    hedging, and a possible catalyst (esp. if earnings are near). Explicitly
    framed as inference from aggregate volume x price, not real order-flow
    tagging -- the bot has no trade-level tape.
    """
    cp = "看涨期权(Call)" if option_type == "CALL" else "看跌期权(Put)"
    signal_str = "、".join(signals) if signals else "无"
    vol_oi = metrics.get("vol_oi_ratio")
    parts = [
        f"合约: {contract_label}（{cp}）",
        f"触发信号: {signal_str}",
        f"成交量: {metrics.get('volume')}",
        f"持仓量: {metrics.get('open_interest')}",
    ]
    if vol_oi is not None:
        parts.append(f"量/仓比: {vol_oi:.1f}")
    if metrics.get("notional") is not None:
        parts.append(f"名义成交额(估算): ${metrics['notional']:,.0f}")
    if metrics.get("iv") is not None:
        parts.append(f"隐含波动率: {metrics['iv']:.1f}%")
    if metrics.get("iv_jump") is not None:
        parts.append(f"IV变动: {metrics['iv_jump']:+.1f}")
    if underlying_price is not None:
        parts.append(f"正股现价: ${underlying_price:,.2f}")
    if earnings_date:
        parts.append(f"临近财报日: {earnings_date}")
    data_block = "\n".join(parts)

    prompt = (
        "你是一名期权异动分析助手。以下是一条被系统标记为异常的美股期权活动数据。"
        "请用中文给出简洁解读，不超过4条要点，每条不超过30字。\n\n"
        "解读应包括：方向性倾向(看涨/看跌)、更像投机还是对冲、可能的催化剂"
        "（若临近财报请指出）。\n\n"
        "重要：名义成交额是用 成交量×价格×合约乘数 估算的，并非真实的大单/扫单逐笔数据，"
        "解读时不要夸大为「机构大单」的确定性。不要提供投资建议。\n\n"
        f"数据：\n{data_block}\n\n"
        "请用简洁的中文bullet point列出。"
    )
    return await _call_deepseek([{"role": "user", "content": prompt}])
