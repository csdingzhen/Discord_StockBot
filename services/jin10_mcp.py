"""
jin10_mcp.py — minimal MCP client for Jin10's free retail flash-news/calendar server.

Protocol: JSON-RPC 2.0 over MCP "Streamable HTTP" transport, responses framed
as SSE ("data: {...}" lines) even for single request/response calls.

Handshake: initialize -> notifications/initialized -> tools/call.
Auth: Authorization: Bearer <token>, plus an Mcp-Session-Id captured from the
initialize response and reused on every later call in the same session.

Confirmed against the live server (2026-06-24): cursor pagination on
list_flash walks *backward* into history, so polling for new items means
calling list_flash() with no cursor on every tick and diffing against
already-seen URLs — cursor is only useful for one-time backfill.
"""
import json

import aiohttp

_MCP_URL = "https://mcp.jin10.com/mcp"
_PROTOCOL_VERSION = "2025-11-25"


def _parse_sse(body: str) -> dict:
    """Pull the JSON payload out of a 'data: {...}' SSE-framed response body."""
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise ValueError(f"No data line found in MCP response: {body!r}")


class Jin10MCPClient:
    """One MCP session against Jin10's flash/news/calendar server. Use as an async context manager."""

    def __init__(self, token: str):
        self._token = token
        self._session: aiohttp.ClientSession | None = None
        self._mcp_session_id: str | None = None
        self._next_id = 1

    async def __aenter__(self) -> "Jin10MCPClient":
        self._session = aiohttp.ClientSession()
        await self._initialize()
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id
        return headers

    async def _post(self, payload: dict) -> dict:
        async with self._session.post(_MCP_URL, headers=self._headers(), json=payload) as resp:
            body = await resp.text()
            if self._mcp_session_id is None and "Mcp-Session-Id" in resp.headers:
                self._mcp_session_id = resp.headers["Mcp-Session-Id"]
            if resp.status != 200:
                raise RuntimeError(f"Jin10 MCP error {resp.status}: {body}")
            return _parse_sse(body)

    async def _initialize(self):
        await self._post({
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "discord-stockbot", "version": "0.1"},
            },
        })
        self._next_id += 1
        # Notification — no id, no response body to parse.
        async with self._session.post(
            _MCP_URL,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        ):
            pass

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool and return its structured `data` payload."""
        envelope = await self._post({
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        self._next_id += 1
        result = envelope["result"]
        if result.get("isError"):
            raise RuntimeError(f"Jin10 MCP tool error from '{name}': {result}")
        return result["structuredContent"]["data"]

    async def list_flash(self, cursor: str | None = None) -> dict:
        """Returns {'items': [{'content','time','url'}...], 'next_cursor': str, 'has_more': bool}.

        No cursor = newest batch. Pass a previous next_cursor only to page
        backward into history — it is not a "continue from last poll" token.
        """
        return await self.call_tool("list_flash", {"cursor": cursor} if cursor else {})

    async def search_flash(self, keyword: str) -> dict:
        """Returns {'items': [...]}, capped at 150 results, no pagination."""
        return await self.call_tool("search_flash", {"keyword": keyword})
