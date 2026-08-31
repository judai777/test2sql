"""LLM 客户端：重试、工具调用解析、流式（全部走 MockTransport，不碰真实 API）。"""
from __future__ import annotations

import json

import httpx
import pytest

from t2s.config import LLMConfig
from t2s.llm import ChatMessage, LLMClient, LLMError
from t2s.llm.types import LLMResponse, ToolCall


def make_client(handler, delays=(0.0, 0.0)) -> LLMClient:
    return LLMClient(LLMConfig(api_key="k", retry_delays=delays), transport=httpx.MockTransport(handler))


def test_retry_on_500_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "你好"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        })

    client = make_client(handler)
    resp = client.chat([ChatMessage(role="user", content="hi")])
    assert resp.content == "你好" and resp.usage.total_tokens == 3 and calls["n"] == 2


def test_tool_calls_parsed_and_sent():
    seen_tools = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        seen_tools["tools"] = body.get("tools")
        seen_tools["tool_choice"] = body.get("tool_choice")
        return httpx.Response(200, json={
            "choices": [{
                "message": {"tool_calls": [{"id": "call_1", "type": "function",
                                            "function": {"name": "get_schema", "arguments": '{"table": "trades"}'}}]},
                "finish_reason": "tool_calls",
            }]
        })

    client = make_client(handler)
    tools = [{"type": "function", "function": {"name": "get_schema", "parameters": {}}}]
    resp = client.chat([ChatMessage(role="user", content="查成交表")], tools=tools)
    assert seen_tools["tool_choice"] == "auto" and seen_tools["tools"] == tools
    assert resp.should_execute_tools
    assert resp.tool_calls[0].name == "get_schema" and resp.tool_calls[0].arguments == {"table": "trades"}


def test_malformed_arguments_preserved():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"tool_calls": [{"id": "1", "type": "function",
                                                     "function": {"name": "t", "arguments": '{"bad'}}]},
                       "finish_reason": "tool_calls"}]
        })

    resp = make_client(handler).chat([ChatMessage(role="user", content="x")])
    assert resp.tool_calls[0].arguments == {} and resp.tool_calls[0].malformed_arguments == '{"bad'


def test_4xx_no_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(LLMError, match="不可重试"):
        make_client(handler).chat([ChatMessage(role="user", content="x")])
    assert calls["n"] == 1


def test_retries_exhausted():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="overloaded")

    with pytest.raises(LLMError, match="耗尽"):
        make_client(handler, delays=(0.0,)).chat([ChatMessage(role="user", content="x")])


def test_stream_yields_deltas():
    sse = (
        'data: {"choices": [{"delta": {"content": "你"}}]}\n\n'
        'data: {"choices": [{"delta": {"content": "好"}}]}\n\n'
        'data: {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}\n\n'
        "data: [DONE]\n\n"
    )
    client = make_client(lambda request: httpx.Response(
        200, text=sse, headers={"content-type": "text/event-stream"}))
    assert "".join(client.chat_stream([ChatMessage(role="user", content="x")])) == "你好"
    assert client.last_usage.total_tokens == 3


def test_should_execute_tools_guards_refusal():
    tc = [ToolCall(id="1", name="execute_sql")]
    assert LLMResponse(tool_calls=tc, finish_reason="tool_calls").should_execute_tools
    assert not LLMResponse(tool_calls=tc, finish_reason="content_filter").should_execute_tools
    assert not LLMResponse(finish_reason="stop").should_execute_tools
