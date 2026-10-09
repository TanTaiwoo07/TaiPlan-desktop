"""通用 AI Provider / API 层。

三种协议统一 adapter：
- openai_responses（OpenAI Responses）
- openai_compatible（OpenAI-compatible Chat Completions，如智谱）
- anthropic（Anthropic Messages）

统一返回：
- AIResult(text, usage) 用于 parse
- 或直接文本用于连接测试

本模块不写数据库、不直接创建任务，只负责 HTTP 请求与响应解析。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests


@dataclass
class AIConfig:
    api_type: str = "openai_responses"          # openai_responses / openai_compatible / anthropic
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    display_name: str = ""
    api_key: str = ""
    context_budget: int = 200000
    max_output_tokens: int = 2048
    timeout: int = 60
    # openai-compatible 输出 token 参数
    token_param: str = "auto"                    # auto / max_completion_tokens / max_tokens
    # OpenAI Responses 的 reasoning effort（none 可避免简单请求产生大量 reasoning）
    reasoning_effort: str = "none"


@dataclass
class AIUsage:
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass
class AIResult:
    text: str
    usage: AIUsage = field(default_factory=AIUsage)


class AIClientError(Exception):
    """AI 请求错误（人类可读信息，不包含 API Key）。"""


class AIClient:
    def __init__(self, config: AIConfig):
        self.config = config

    # ---------- 公共入口 ----------

    def test_connection(self) -> str:
        """发送极小请求要求模型回复 OK，返回模型文本。"""
        prompt = "Reply with exactly: OK"
        result = self.request(prompt, max_output_tokens=16)
        return result.text.strip()

    def parse_structured(self, system_prompt: str, user_text: str, schema_hint: str) -> AIResult:
        """用于结构化解析（Quick Task 等）。"""
        return self.request(user_text, system_prompt=system_prompt)

    def request(self, user_prompt: str, system_prompt: Optional[str] = None,
                max_output_tokens: Optional[int] = None) -> AIResult:
        api_type = self.config.api_type
        if api_type == "openai_responses":
            return self._request_with_empty_retry(
                lambda: self._openai_responses_request(user_prompt, system_prompt, max_output_tokens))
        elif api_type == "openai_compatible":
            return self._request_with_empty_retry(
                lambda: self._openai_compatible_request(user_prompt, system_prompt, max_output_tokens))
        elif api_type == "anthropic":
            return self._request_with_empty_retry(
                lambda: self._anthropic_request(user_prompt, system_prompt, max_output_tokens))
        else:
            raise AIClientError(f"未知的 API 类型: {api_type}")

    def _request_with_empty_retry(self, fn):
        """对 HTTP 200 但文本为空的情况最多重试 1 次。

        仅对“空内容”错误重试；401/429/500/timeout 等其他错误不重试。
        """
        def _is_empty_err(e):
            return isinstance(e, AIClientError) and ("空内容" in str(e))

        for attempt in range(2):
            try:
                result = fn()
                if result.text.strip():
                    return result
                # 文本为空（没有抛错但返回空字符串），继续重试
                continue
            except AIClientError as e:
                if _is_empty_err(e) and attempt == 0:
                    continue
                raise
        raise AIClientError("AI 返回了空内容，请重试或切换 API 协议。")

    # ---------- 通用工具 ----------

    def _headers(self, auth_header: str, key: str) -> Dict[str, str]:
        if not key:
            raise AIClientError("API Key 未配置。")
        return {"Authorization": f"{auth_header} {key}", "Content-Type": "application/json"}

    def _post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=self.config.timeout)
        except requests.exceptions.Timeout:
            raise AIClientError("请求超时，请稍后重试。")
        except requests.exceptions.ConnectionError:
            raise AIClientError("网络连接失败，请检查 Base URL 和网络。")
        except requests.exceptions.RequestException as e:
            raise AIClientError(f"网络请求失败：{type(e).__name__}")

        status = resp.status_code
        if status == 401:
            raise AIClientError("401：API Key 无效或未授权。")
        if status == 403:
            raise AIClientError("403：没有访问权限。")
        if status == 404:
            raise AIClientError("404：endpoint 不存在，请检查 Base URL。")
        if status == 429:
            raise AIClientError("429：请求过于频繁，请稍后重试。")
        if status in (500, 502, 503):
            raise AIClientError(f"{status}：服务端错误，请稍后重试。")
        if status >= 400:
            # 尝试提取错误信息（不包含 key）
            body = resp.text[:500]
            raise AIClientError(f"HTTP {status}：{body}")

        try:
            return resp.json()
        except ValueError:
            raise AIClientError("响应不是有效 JSON。")

    # ---------- OpenAI Responses ----------

    def _openai_responses_request(self, user_prompt, system_prompt, max_output_tokens):
        url = self.config.base_url.rstrip("/") + "/responses"
        headers = self._headers("Bearer", self.config.api_key)
        max_out = max_output_tokens if max_output_tokens is not None else self.config.max_output_tokens
        payload = {
            "model": self.config.model,
            "input": user_prompt,
            "max_output_tokens": max_out,
        }
        if getattr(self.config, "reasoning_effort", None):
            payload["reasoning"] = {"effort": self.config.reasoning_effort}
        if system_prompt:
            payload["instructions"] = system_prompt

        data = self._post(url, headers, payload)
        # 提取文本
        text = _extract_openai_responses_text(data)
        usage = _extract_usage(data)
        return AIResult(text=text, usage=usage)

    # ---------- OpenAI Compatible ----------

    def _openai_compatible_request(self, user_prompt, system_prompt, max_output_tokens):
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = self._headers("Bearer", self.config.api_key)
        max_out = max_output_tokens if max_output_tokens is not None else self.config.max_output_tokens

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {"model": self.config.model, "messages": messages}

        # 输出 token 参数映射
        token_param = self.config.token_param
        if token_param == "auto":
            token_param = "max_completion_tokens"

        data = self._try_compatible_request(url, headers, payload, token_param, max_out)

        text = _extract_openai_compatible_text(data)
        usage = _extract_usage(data)
        return AIResult(text=text, usage=usage)

    def _try_compatible_request(self, url, headers, payload, token_param, max_out):
        """openai-compatible 输出 token 参数智能兼容（仅对参数不兼容错误重试一次）。"""
        p = dict(payload)
        p[token_param] = max_out
        try:
            return self._post(url, headers, p)
        except AIClientError as e:
            # 仅对明确的参数不兼容错误（400 + unsupported/unknown/invalid parameter）重试
            msg = str(e)
            if "400" not in msg and "HTTP 400" not in msg:
                raise
            lower = msg.lower()
            if ("unsupported parameter" not in lower
                    and "unknown parameter" not in lower
                    and "invalid parameter" not in lower
                    and token_param not in lower):
                raise
            # 重试一次，换成另一种参数
            alt = "max_tokens" if token_param == "max_completion_tokens" else "max_completion_tokens"
            p2 = dict(payload)
            p2[alt] = max_out
            return self._post(url, headers, p2)

    # ---------- Anthropic ----------

    def _anthropic_request(self, user_prompt, system_prompt, max_output_tokens):
        url = self.config.base_url.rstrip("/") + "/v1/messages"
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if not self.config.api_key:
            raise AIClientError("API Key 未配置。")
        max_out = max_output_tokens if max_output_tokens is not None else self.config.max_output_tokens
        payload = {
            "model": self.config.model,
            "max_tokens": max_out,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt

        data = self._post(url, headers, payload)
        text = _extract_anthropic_text(data)
        usage = _extract_usage(data)
        return AIResult(text=text, usage=usage)


# ---------- 响应文本与 usage 提取 ----------

def _extract_openai_responses_text(data):
    """从 Responses API 响应中提取最终文本。

    兼容两种结构：
    A. top-level output_text
    B. output[] -> message -> content[] -> output_text

    忽略 reasoning / reasoning_text / function_call。
    """
    texts = []

    # A. top-level output_text
    top = data.get("output_text")
    if isinstance(top, str) and top.strip():
        texts.append(top.strip())

    # B. output[] -> message -> content[] -> output_text
    outputs = data.get("output", [])
    if isinstance(outputs, list):
        for item in outputs:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text":
                    t = part.get("text")
                    if t and str(t).strip():
                        texts.append(str(t).strip())

    text = "\n".join(texts).strip()
    if text:
        return text

    output_types = [
        (item.get("type") if isinstance(item, dict) else type(item).__name__)
        for item in outputs
    ] if isinstance(outputs, list) else []
    diag = {
        "output_item_types": output_types,
        "err": data.get("error") if isinstance(data, dict) else None,
        "incomplete_details": data.get("incomplete_details") if isinstance(data, dict) else None,
    }
    raise AIClientError(f"AI 返回了空内容（诊断：{diag}）")


def _extract_openai_compatible_text(data):
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            return "\n".join(parts).strip()
    except (KeyError, IndexError, TypeError):
        pass
    raise AIClientError("AI 返回了空内容。")


def _extract_anthropic_text(data):
    try:
        content = data.get("content", [])
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        text = "\n".join(parts).strip()
        if text:
            return text
    except (AttributeError, TypeError):
        pass
    raise AIClientError("AI 返回了空内容。")


def _extract_usage(data) -> AIUsage:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not usage:
        return AIUsage()
    return AIUsage(
        input_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
        output_tokens=usage.get("output_tokens") or usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )
