import json
import unittest
from unittest.mock import patch

from services import jin10_mcp as mcp
from tests.http_fakes import response, session


class MCPResponseTests(unittest.TestCase):
    def test_json_and_multiline_sse_with_notifications(self):
        expected = {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}
        self.assertEqual(mcp._parse_response(json.dumps(expected), 2), expected)
        body = ': heartbeat\n\ndata: {"method":"notifications/progress"}\n\n'
        body += 'data: {"jsonrpc":"2.0","id":2,\ndata: "result":{"ok":true}}\n\n'
        self.assertEqual(mcp._parse_response(body, 2), expected)

    def test_rpc_error_and_unexpected_response_are_sanitized(self):
        for body in ['{"id":2,"error":{"code":-32603,"message":"secret"}}',
                     '<html>secret</html>', '{"id":3,"result":{}}', 'data: invalid\n\n']:
            with self.subTest(body=body), self.assertRaises(mcp.Jin10Error) as error:
                mcp._parse_response(body, 2)
            self.assertNotIn("secret", str(error.exception))


class MCPClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_session_and_tool_text_fallback(self):
        data = {"items": [{"url": "https://example.test/1", "time": "now", "content": "data"}],
                "has_more": False}
        http = session(
            response(body={"id": 1, "result": {"protocolVersion": "2025-11-25"}},
                     headers={"Mcp-Session-Id": "test-session"}),
            response(status=202),
            response(body={"id": 2, "result": {"content": [
                {"type": "text", "text": json.dumps({"data": data})}]}}),
        )
        with patch.object(mcp.aiohttp, "ClientSession", return_value=http) as factory:
            async with mcp.Jin10MCPClient("fake-token") as client:
                self.assertEqual(await client.list_flash(), data)
        self.assertEqual(factory.call_args.kwargs["timeout"].total, 30)
        headers = http.post.call_args.kwargs["headers"]
        self.assertEqual(headers["Mcp-Session-Id"], "test-session")
        self.assertEqual(headers["MCP-Protocol-Version"], "2025-11-25")
        http.close.assert_awaited_once()

    async def test_failed_initialization_closes_session(self):
        http = session(response(status=401, text="secret-token"))
        with patch.object(mcp.aiohttp, "ClientSession", return_value=http):
            with self.assertRaisesRegex(mcp.Jin10Error, "initialize: HTTP 401"):
                async with mcp.Jin10MCPClient("fake-token"):
                    self.fail("must not enter")
        http.close.assert_awaited_once()

    async def test_notification_http_failure_is_not_ignored(self):
        http = session(response(body={"id": 1, "result": {}}), response(status=429))
        with patch.object(mcp.aiohttp, "ClientSession", return_value=http):
            with self.assertRaisesRegex(mcp.Jin10Error, "initialized: HTTP 429"):
                async with mcp.Jin10MCPClient("fake-token"):
                    self.fail("must not enter")
        http.close.assert_awaited_once()

    async def test_structured_tool_response_and_item_validation(self):
        for data, valid in [({"items": []}, True), ({"items": [{}]}, False), ({}, False)]:
            http = session(response(body={"id": 1, "result": {"structuredContent": {"data": data}}}))
            client = mcp.Jin10MCPClient("fake")
            client._session = http
            if valid:
                self.assertEqual(await client.list_flash(), data)
            else:
                with self.assertRaisesRegex(mcp.Jin10Error, "invalid items"):
                    await client.list_flash()
