"""LLM 消息与响应的强类型模型。

设计要点（nanobot 调研报告 §3.2 的不变量前置到这里）：
- should_execute_tools：带 tool_calls 但 finish_reason 不允许执行时，调用方必须拒绝执行。

ChatMessage 已迁移至 models/messages.py（跨层模型，ADR-005）；此处 re-export 保持兼容。
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from t2s.models.messages import ChatMessage, Role  # noqa: F401  (re-export)

_EXECUTABLE_FINISH = {"tool_calls", "function_call", "stop"}


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # OpenAI 协议里 arguments 是 JSON 字符串；解析失败时保留原文供上层诊断
    malformed_arguments: str | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)

    @property
    def should_execute_tools(self) -> bool:
        """有 tool_calls 且 finish_reason 允许执行（拒绝带工具调用的 refusal，nanobot 不变量）。"""
        return bool(self.tool_calls) and self.finish_reason in _EXECUTABLE_FINISH
