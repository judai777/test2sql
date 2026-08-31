"""MCP 桥接：把 MCP 服务端的工具适配成 ToolRegistry（coder 子 agent 的工具面）。

传输：fastmcp 内存传输（同进程走完整 MCP 协议）；生产可无缝换 stdio/SSE 连外部服务，
代码不变。错误语义经 JSON 信封跨协议保留。
"""
from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Callable

from t2s.tools.base import Tool, ToolResult
from t2s.tools.registry import ToolRegistry

_SPECS = {
    "get_schema": ("取某张表的完整 DDL（列注释/枚举/外键）、行数与样例（经 MCP 调用数据库）",
                   {"type": "object", "additionalProperties": False,
                    "properties": {"table": {"type": "string", "description": "表名"}},
                    "required": ["table"]}),
    "validate_sql": ("执行前白盒校验 SQL：语法/只读断言/表列存在性/权限白名单（经 MCP）",
                     {"type": "object", "additionalProperties": False,
                      "properties": {"sql": {"type": "string", "description": "完整 SQL"}},
                      "required": ["sql"]}),
    "execute_sql": ("在只读连接上执行 SQL（自动 LIMIT/行数上限/超时；写操作硬边界拒绝）（经 MCP）",
                    {"type": "object", "additionalProperties": False,
                     "properties": {"sql": {"type": "string", "description": "已通过 validate_sql 的完整 SQL"}},
                     "required": ["sql"]}),
    "ask_user": ("信息不足时向用户发起一次澄清（human-in-the-loop）（经 MCP）",
                 {"type": "object", "additionalProperties": False,
                  "properties": {"question": {"type": "string", "description": "澄清问题"},
                                 "options": {"type": "array", "items": {"type": "string"}}},
                  "required": ["question"]}),
}


class _MCPBackedTool(Tool):
    def __init__(self, name: str, description: str, parameters: dict,
                 runner: Callable[[dict], ToolResult]) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._runner = runner

    def execute(self, ctx, **params) -> ToolResult:  # noqa: ARG002 —— 服务端自带 ctx
        return self._runner(params)


def build_mcp_backed_registry(server) -> ToolRegistry:
    """把 fastmcp server 的工具适配为 ToolRegistry（coder 子 agent 消费）。"""

    def call(tool: str, args: dict) -> ToolResult:
        async def _run() -> str:
            from fastmcp import Client
            async with Client(server) as client:
                result = await client.call_tool(tool, args)
                return "".join(part.text for part in result.content
                               if hasattr(part, "text"))

        envelope = json.loads(asyncio.run(_run()))
        output = envelope["output"]
        return ToolResult.error(output) if not envelope["ok"] else ToolResult.ok(output)

    registry = ToolRegistry()
    for name, (description, parameters) in _SPECS.items():
        registry.register(_MCPBackedTool(name, description, parameters,
                                         partial(call, name)))
    return registry
