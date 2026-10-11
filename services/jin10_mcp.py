"""
jin10_mcp.py — minimal MCP client for Jin10's free retail flash-news/calendar server.

Protocol: JSON-RPC 2.0 over MCP Streamable HTTP, accepting JSON or SSE.

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


class Jin10Error(RuntimeError):
    """Transport/protocol diagnostics without echoing tokens or response bodies."""


def _parse_response(body: str, request_id: int) -> dict:
    if body.lstrip().startswith("{"):
        try:
            messages = [json.loads(body)]
        except ValueError as exc:
            raise Jin10Error("invalid JSON response") from exc
    else:
        messages = []
        lines = []
        for line in body.splitlines() + [""]:
            if line.startswith("data:"):
                lines.append(line[5:].lstrip(" "))
            elif not line and lines:
                data = "\n".join(lines)
                lines = []
                if not data.strip() or data == "[DONE]":
                    continue
                try:
                    messages.append(json.loads(data))
                except ValueError as exc:
                    raise Jin10Error("invalid SSE JSON response") from exc
    for message in messages:
        if isinstance(message, dict) and message.get("id") == request_id:
            if "error" in message:
                error = message["error"]
                code = error.get("code") if isinstance(error, dict) else None
                code = code if isinstance(code, int) else "unknown"
                raise Jin10Error(f"JSON-RPC error {code}")
            if not isinstance(message.get("result"), dict):
                raise Jin10Error("missing JSON-RPC result")
            return message
    raise Jin10Error("missing matching JSON-RPC response (JSON/SSE)")


class Jin10MCPClient:
    """One MCP session against Jin10's flash/news/calendar server. Use as an async context manager."""

    def __init__(self, token: str):
        self._token = token
        self._session: aiohttp.ClientSession | None = None
        self._mcp_session_id: str | None = None
        self._next_id = 1
        self._protocol_version = _PROTOCOL_VERSION

    async def __aenter__(self) -> "Jin10MCPClient":
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        try:
            await self._initialize()
        except BaseException:
            await self._session.close()
            raise
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self._protocol_version,
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
                raise Jin10Error(f"{payload['method']}: HTTP {resp.status}")
            try:
                return _parse_response(body, payload["id"])
            except Jin10Error as exc:
                raise Jin10Error(f"{payload['method']}: {exc}") from exc

    async def _initialize(self):
        initialized = await self._post({
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "discord-stockbot", "version": "0.1"},
            },
        })
        self._protocol_version = initialized["result"].get("protocolVersion", _PROTOCOL_VERSION)
        self._next_id += 1
        # Notification — no id, no response body to parse.
        async with self._session.post(
            _MCP_URL,
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        ) as resp:
            if resp.status not in (200, 202, 204):
                raise Jin10Error(f"notifications/initialized: HTTP {resp.status}")

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
            raise Jin10Error(f"tool {name}: isError=true")
        structured = result.get("structuredContent")
        if structured is None:
            for block in result.get("content", []):
                if block.get("type") == "text":
                    try:
                        structured = json.loads(block["text"])
                        break
                    except (KeyError, ValueError):
                        continue
        if isinstance(structured, dict):
            data = structured.get("data", structured)
            if isinstance(data, dict):
                return data
        raise Jin10Error(f"tool {name}: missing structured data")

    async def list_flash(self, cursor: str | None = None) -> dict:
        """Returns {'items': [{'content','time','url'}...], 'next_cursor': str, 'has_more': bool}.

        No cursor = newest batch. Pass a previous next_cursor only to page
        backward into history — it is not a "continue from last poll" token.
        """
        data = await self.call_tool("list_flash", {"cursor": cursor} if cursor else {})
        items = data.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, dict) or any(
                not isinstance(item.get(key), str) or not item[key]
                for key in ("url", "content", "time")
            ) for item in items
        ):
            raise Jin10Error("tool list_flash: invalid items schema")
        return data

    async def search_flash(self, keyword: str) -> dict:
        """Returns {'items': [...]}, capped at 150 results, no pagination."""
        return await self.call_tool("search_flash", {"keyword": keyword})
