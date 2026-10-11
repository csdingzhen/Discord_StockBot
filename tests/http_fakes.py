"""Small aiohttp boundary fake; business logic and SQLite remain real in tests."""
import json
from unittest.mock import AsyncMock, MagicMock, Mock


def response(status=200, body=None, headers=None, text=None):
    result = MagicMock()
    result.status = status
    result.headers = headers or {}
    result.json = AsyncMock(return_value=body)
    result.text = AsyncMock(return_value=text if text is not None else json.dumps(body))
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    return result


def session(*responses):
    result = MagicMock()
    result.post = Mock(side_effect=list(responses))
    result.close = AsyncMock()
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    return result
