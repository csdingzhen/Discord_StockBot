import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from services import llm_client, metrics
from tests.http_fakes import response, session


def completion(content="answer", **kwargs):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.002},
        **kwargs,
    }


class LLMClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target, value in [
            ("OPENROUTER_API_KEY", "test-key-not-a-secret"),
            ("LLM_MODEL", "test/model"), ("LLM_TIMEOUT_SECONDS", 2),
            ("LLM_MAX_TOKENS", 2048),
            ("LLM_REASONING_ENABLED", False),
        ]:
            p = patch.object(llm_client.config, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(llm_client, "_cooldown_until", 0)
        p.start()
        self.addCleanup(p.stop)

    async def test_openrouter_payload_and_metrics(self):
        http = session(response(body=completion('{"level": 2, "category": "AI"}')))
        with patch.object(llm_client.aiohttp, "ClientSession", return_value=http) as factory, \
                patch.object(llm_client, "record_llm_cost") as cost:
            self.assertEqual(await llm_client.classify_flash("test news"), {"level": 2, "category": "AI"})
        request = http.post.call_args
        self.assertEqual(request.args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request.kwargs["json"]["model"], "test/model")
        self.assertEqual(request.kwargs["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(request.kwargs["json"]["provider"], {"require_parameters": True})
        self.assertEqual(request.kwargs["json"]["reasoning"], {"enabled": False})
        self.assertEqual(factory.call_args.kwargs["timeout"].total, 2)
        cost.assert_called_once_with("test/model", 10, 5, 0.002)

    async def test_no_key_fails_without_network(self):
        with patch.object(llm_client.config, "OPENROUTER_API_KEY", ""), \
                patch.object(llm_client.aiohttp, "ClientSession") as factory:
            with self.assertRaisesRegex(llm_client.LLMError, "not configured"):
                await llm_client.analyze_premarket("data")
        factory.assert_not_called()

    async def test_auth_and_credit_errors_are_not_retried_or_leaked(self):
        for code in (401, 402, 403):
            with self.subTest(status=code), patch.object(llm_client, "_cooldown_until", 0):
                resp = response(status=code, text="sensitive provider body")
                http = session(resp)
                with patch.object(llm_client.aiohttp, "ClientSession", return_value=http), \
                        patch.object(llm_client.time, "monotonic", return_value=100):
                    with self.assertRaises(llm_client.LLMError) as error:
                        await llm_client.analyze_premarket("data")
                    self.assertEqual(error.exception.status, code)
                    self.assertNotIn("sensitive", str(error.exception))
                    with self.assertRaisesRegex(llm_client.LLMError, "temporarily disabled"):
                        await llm_client.classify_flash("other data")
                    self.assertEqual(llm_client._cooldown_until, 400)
                self.assertEqual(http.post.call_count, 1)

    async def test_rate_limit_respects_retry_after_and_recovers(self):
        http = session(response(status=429, headers={"Retry-After": "120"}), response(body=completion()))
        with patch.object(llm_client.aiohttp, "ClientSession", return_value=http), \
                patch.object(llm_client.time, "monotonic", return_value=10) as clock:
            with self.assertRaises(llm_client.LLMError):
                await llm_client.analyze_premarket("data")
            self.assertEqual(llm_client._cooldown_until, 130)
            clock.return_value = 131
            self.assertEqual(await llm_client.analyze_premarket("data"), "answer")

    async def test_timeout_is_bounded_failure(self):
        http = session()
        http.post.side_effect = asyncio.TimeoutError
        with patch.object(llm_client.aiohttp, "ClientSession", return_value=http):
            with self.assertRaisesRegex(llm_client.LLMError, "TimeoutError"):
                await llm_client.analyze_premarket("data")

    async def test_invalid_output_and_http200_errors_are_failures(self):
        bodies = [{"error": {"code": 502}}, {}, completion(None), completion(""),
                  completion("not json"), completion("[]")]
        for body in bodies:
            with self.subTest(body=body), patch.object(llm_client, "_cooldown_until", 0):
                http = session(response(body=body))
                with patch.object(llm_client.aiohttp, "ClientSession", return_value=http):
                    with self.assertRaises(llm_client.LLMError):
                        await llm_client.classify_flash("data")

    async def test_classifier_rejects_invalid_level_and_followup_string(self):
        with patch.object(llm_client, "_call_llm", new=AsyncMock(return_value='{"level": true}')):
            with self.assertRaises(llm_client.LLMError):
                await llm_client.classify_flash("data")
        with patch.object(llm_client, "_call_llm", new=AsyncMock(return_value='{"is_followup": "false"}')):
            with self.assertRaises(llm_client.LLMError):
                await llm_client.is_followup_story("data", ["prior"])

    async def test_all_features_share_transport(self):
        result = '{"level": 2, "category": "AI", "is_followup": false}'
        with patch.object(llm_client, "_call_llm", new=AsyncMock(return_value=result)) as call:
            await llm_client.analyze_ticker("AAPL", "data")
            await llm_client.analyze_premarket("data")
            await llm_client.classify_flash("data")
            await llm_client.is_followup_story("data", ["prior"])
            await llm_client.summarize_flash_alert("data")
            await llm_client.summarize_flash_digest([{"time": "now", "content": "data"}])
            await llm_client.analyze_earnings_reaction("AAPL", 1, 1, 1, 1, 1)
            await llm_client.analyze_sec_filing("AAPL", "8-K", "data")
            await llm_client.analyze_options_anomaly("test", "CALL", [], {}, 10)
            self.assertEqual(call.await_count, 9)

    def test_reported_cost_takes_precedence_over_estimate(self):
        counter = Mock()
        with patch.object(metrics, "llm_cost_usd_total", counter), \
                patch.object(metrics, "_PRICE_PROMPT_PER_1M", 10), \
                patch.object(metrics, "_PRICE_COMPLETION_PER_1M", 10):
            metrics.record_llm_cost("test", 100, 100, 0)
            counter.labels.return_value.inc.assert_called_once_with(0)
            counter.reset_mock()
            metrics.record_llm_cost("test", 100, 100, 0.25)
            counter.labels.return_value.inc.assert_called_once_with(0.25)
