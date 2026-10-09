"""AI Client 单元测试（使用 mock，不发送真实 API 请求）。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_ai_client
"""

import json
import unittest
from unittest import mock

from taiplan.ai_client import AIConfig, AIClient, AIClientError, AIResult, AIUsage


def _config(**kwargs):
    defaults = dict(
        api_type="openai_responses",
        base_url="https://api.openai.com/v1",
        model="test-model",
        api_key="sk-test",
        max_output_tokens=2048,
        timeout=10,
    )
    defaults.update(kwargs)
    return AIConfig(**defaults)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class AIClientPayloadTest(unittest.TestCase):
    """测试三种协议的请求 payload 与参数映射。"""

    @mock.patch("taiplan.ai_client.requests.post")
    def test_openai_responses_payload(self, mock_post):
        mock_post.return_value = FakeResponse(200, {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
            "usage": {"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
        })
        client = AIClient(_config())
        r = client.request("hi", system_prompt="sys", max_output_tokens=16)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(kwargs["json"]["max_output_tokens"], 16)
        self.assertEqual(kwargs["json"]["instructions"], "sys")
        self.assertEqual(r.text, "OK")
        self.assertEqual(r.usage.input_tokens, 5)

    @mock.patch("taiplan.ai_client.requests.post")
    def test_openai_compatible_payload(self, mock_post):
        mock_post.return_value = FakeResponse(200, {
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        })
        client = AIClient(_config(api_type="openai_compatible", base_url="https://open.bigmodel.cn/api/paas/v4"))
        r = client.request("hi", max_output_tokens=16)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://open.bigmodel.cn/api/paas/v4/chat/completions")
        # 默认 auto → max_completion_tokens
        self.assertIn("max_completion_tokens", kwargs["json"])
        self.assertEqual(kwargs["json"]["max_completion_tokens"], 16)
        self.assertEqual(r.text, "OK")
        self.assertEqual(r.usage.input_tokens, 5)

    @mock.patch("taiplan.ai_client.requests.post")
    def test_anthropic_payload(self, mock_post):
        mock_post.return_value = FakeResponse(200, {
            "content": [{"type": "text", "text": "OK"}],
            "usage": {"input_tokens": 5, "output_tokens": 1},
        })
        client = AIClient(_config(api_type="anthropic", base_url="https://api.anthropic.com"))
        r = client.request("hi", max_output_tokens=32)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(kwargs["json"]["max_tokens"], 32)
        self.assertEqual(kwargs["headers"]["x-api-key"], "sk-test")
        self.assertEqual(kwargs["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(r.text, "OK")

    @mock.patch("taiplan.ai_client.requests.post")
    def test_max_output_anthropic_mapping(self, mock_post):
        """统一 max_output_tokens → Anthropic max_tokens。"""
        mock_post.return_value = FakeResponse(200, {"content": [{"type": "text", "text": "OK"}]})
        client = AIClient(_config(api_type="anthropic", base_url="https://api.anthropic.com"))
        client.request("hi")  # 用默认 max_output_tokens=2048
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs["json"]["max_tokens"], 2048)


class AIClientErrorTest(unittest.TestCase):
    """测试错误处理与 token 参数 fallback。"""

    @mock.patch("taiplan.ai_client.requests.post")
    def test_401(self, mock_post):
        mock_post.return_value = FakeResponse(401, None, "unauthorized")
        client = AIClient(_config())
        with self.assertRaises(AIClientError) as ctx:
            client.request("hi")
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("sk-test", str(ctx.exception))  # 不泄露 key

    @mock.patch("taiplan.ai_client.requests.post")
    def test_429(self, mock_post):
        mock_post.return_value = FakeResponse(429, None, "rate limit")
        client = AIClient(_config())
        with self.assertRaises(AIClientError) as ctx:
            client.request("hi")
        self.assertIn("429", str(ctx.exception))

    @mock.patch("taiplan.ai_client.requests.post")
    def test_timeout(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.Timeout("timeout")
        client = AIClient(_config())
        with self.assertRaises(AIClientError):
            client.request("hi")

    @mock.patch("taiplan.ai_client.requests.post")
    def test_json_parse_failure(self, mock_post):
        mock_post.return_value = FakeResponse(200, None, "not json")
        client = AIClient(_config())
        with self.assertRaises(AIClientError):
            client.request("hi")

    @mock.patch("taiplan.ai_client.requests.post")
    def test_max_completion_tokens_fallback(self, mock_post):
        """openai-compatible：max_completion_tokens 不兼容时自动重试 max_tokens。"""
        # 第一次返回 400 指向 max_completion_tokens，第二次成功
        resp_bad = FakeResponse(400, None, "400 unsupported parameter: max_completion_tokens")
        resp_ok = FakeResponse(200, {"choices": [{"message": {"content": "OK"}}]})
        mock_post.side_effect = [resp_bad, resp_ok]

        client = AIClient(_config(api_type="openai_compatible", base_url="https://x.com/v1"))
        r = client.request("hi")
        self.assertEqual(r.text, "OK")
        # 第一次用了 max_completion_tokens，第二次用 max_tokens
        first_json = mock_post.call_args_list[0].kwargs["json"]
        second_json = mock_post.call_args_list[1].kwargs["json"]
        self.assertIn("max_completion_tokens", first_json)
        self.assertIn("max_tokens", second_json)




class AIClientResponsesTextTest(unittest.TestCase):
    """Responses API 文本提取的边界情况。"""

    @mock.patch("taiplan.ai_client.requests.post")
    def test_extract_message_ignores_reasoning(self, mock_post):
        """output 包含 reasoning + message，只提取 message 的 output_text。"""
        mock_post.return_value = FakeResponse(200, {
            "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking..."}]},
                {"type": "message", "content": [
                    {"type": "output_text", "text": "OK"},
                ]},
                {"type": "function_call", "name": "f", "arguments": "{}"},
            ],
        })
        client = AIClient(_config())
        r = client.request("hi")
        self.assertEqual(r.text, "OK")

    @mock.patch("taiplan.ai_client.requests.post")
    def test_top_level_output_text(self, mock_post):
        """兼容 top-level output_text。"""
        mock_post.return_value = FakeResponse(200, {"output_text": "OK"})
        client = AIClient(_config())
        r = client.request("hi")
        self.assertEqual(r.text, "OK")

    @mock.patch("taiplan.ai_client.requests.post")
    def test_empty_result_retries_once(self, mock_post):
        """第一次空内容，第二次成功。"""
        resp_empty = FakeResponse(200, {"output": [{"type": "reasoning"}]})
        resp_ok = FakeResponse(200, {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]
        })
        mock_post.side_effect = [resp_empty, resp_ok]
        client = AIClient(_config())
        r = client.request("hi")
        self.assertEqual(r.text, "OK")
        self.assertEqual(mock_post.call_count, 2)

    @mock.patch("taiplan.ai_client.requests.post")
    def test_empty_result_after_retry_raises(self, mock_post):
        """两次都空，抛错。"""
        resp_empty = FakeResponse(200, {"output": [{"type": "reasoning"}]})
        mock_post.side_effect = [resp_empty, resp_empty]
        client = AIClient(_config())
        with self.assertRaises(AIClientError) as ctx:
            client.request("hi")
        self.assertIn("空内容", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 2)

    @mock.patch("taiplan.ai_client.requests.post")
    def test_connection_test_max_output_16(self, mock_post):
        """连接测试强制 max_output_tokens=16，且带 reasoning effort none。"""
        mock_post.return_value = FakeResponse(200, {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]
        })
        client = AIClient(_config())
        client.test_connection()
        json_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(json_payload["max_output_tokens"], 16)
        self.assertEqual(json_payload.get("reasoning"), {"effort": "none"})


if __name__ == "__main__":
    unittest.main()
