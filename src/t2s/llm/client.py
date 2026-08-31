"""OpenAI 兼容 LLM 客户端（httpx 同步版）。

- 重试：网络错误与 408/429/5xx 按 retry_delays 退避，遵循 Retry-After；4xx 业务错误不重试。
- 超时：连接 10s / 读 T2S_LLM_TIMEOUT_S。
- 流式：SSE 逐 delta yield；流式请求失败由上层整体重试（M2 引擎兜底）。
- transport 参数仅供测试注入 httpx.MockTransport。
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx

from t2s.config import LLMConfig
from t2s.llm.types import ChatMessage, LLMResponse, ToolCall, Usage

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """LLM 调用最终失败（重试耗尽或不可重试错误）。调用方应转成错误视图/观察，而不是崩掉 turn。"""


class LLMClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.last_usage = Usage()
        self._http = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(connect=10.0, read=config.timeout_s, write=30.0, pool=10.0),
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # ---------- 内部 ----------

    def _payload(self, messages: list[ChatMessage], tools: list[dict] | None, temperature: float | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [m.to_api() for m in messages],
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _post_once(self, payload: dict[str, Any]) -> httpx.Response:
        return self._http.post("/chat/completions", json=payload)

    def _sleep(self, response: httpx.Response) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 30.0))
                return
            except ValueError:
                pass
        time.sleep(self.config.retry_delays[-1])

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        usage_raw = data.get("usage") or {}
        self.last_usage = Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
            total_tokens=usage_raw.get("total_tokens", 0),
        )
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or ""
            try:
                arguments = json.loads(raw_args) if raw_args else {}
                malformed = None
            except json.JSONDecodeError:
                arguments, malformed = {}, raw_args
            tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=arguments, malformed_arguments=malformed))
        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            usage=self.last_usage,
        )

    # ---------- 公开 API ----------

    def chat(self, messages: list[ChatMessage], tools: list[dict] | None = None, temperature: float | None = None) -> LLMResponse:
        payload = self._payload(messages, tools, temperature)
        delays = (None,) + tuple(self.config.retry_delays)
        last_error: str | None = None
        for attempt, delay in enumerate(delays):
            if delay:
                time.sleep(delay)
            try:
                resp = self._post_once(payload)
            except httpx.TransportError as e:
                last_error = f"网络错误: {type(e).__name__}: {e}"
                continue
            if resp.status_code == 200:
                return self._parse_response(resp.json())
            if resp.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                self._sleep(resp)
                continue
            raise LLMError(f"LLM 请求失败（不可重试）HTTP {resp.status_code}: {resp.text[:300]}")
        raise LLMError(f"LLM 请求失败（重试 {attempt + 1} 次耗尽）: {last_error}")

    def chat_stream(self, messages: list[ChatMessage], tools: list[dict] | None = None, temperature: float | None = None) -> Iterator[str]:
        payload = self._payload(messages, tools, temperature)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        with self._http.stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", errors="replace")[:300]
                raise LLMError(f"LLM 流式请求失败 HTTP {resp.status_code}: {body}")
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                if obj.get("usage"):
                    self.last_usage = Usage(
                        prompt_tokens=obj["usage"].get("prompt_tokens", 0),
                        completion_tokens=obj["usage"].get("completion_tokens", 0),
                        total_tokens=obj["usage"].get("total_tokens", 0),
                    )
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
