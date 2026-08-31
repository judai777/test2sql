"""跨层共享的消息模型：manager 装配、executor 消费（ADR-005：跨层模型入 models/）。"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    """对话消息。tool_calls 兼容两种形态：LLM 协议的 ToolCall 对象（assistant 回填）
    与已序列化的 dict（测试构造）；to_api() 统一转成 OpenAI 协议格式。"""

    role: Role
    content: str | None = None
    tool_calls: list[Any] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_api(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            serialized = []
            for tc in self.tool_calls:
                if hasattr(tc, "model_dump"):  # llm 协议 ToolCall 对象
                    serialized.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name,
                                     "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    })
                else:  # 已是 dict
                    serialized.append(tc)
            out["tool_calls"] = serialized
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            out["name"] = self.name
        return out
