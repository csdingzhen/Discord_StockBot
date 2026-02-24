"""
news_feed.py — async News API wrapper.
Requires NEWS_API_KEY in .env.  Returns [] gracefully if key is absent.
"""
import aiohttp
import config

_BASE = "https://newsapi.org/v2/everything"


async def get_headlines(query: str, page_size: int = 5) -> list[dict]:
    """
    Fetch recent news articles matching *query*.
    Returns a list of article dicts with keys: title, url, publishedAt, source.
    """
    if not config.NEWS_API_KEY:
        return []

    params = {
        "q": query,
        "sortBy": "publishedAt",
        "pageSize": page_size,
        "language": "en",
        "apiKey": config.NEWS_API_KEY,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(_BASE, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("articles", [])
