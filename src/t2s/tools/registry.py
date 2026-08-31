"""工具注册中心（nanobot `tools/registry.py` 模式简化）。

执行前统一协议：未知工具→模糊建议；JSON 字符串参数→解析；{"arguments":{}} 包装→解包；
cast→validate；执行异常→错误观察（永不向引擎抛异常）。
"""
from __future__ import annotations

import difflib
import json
from typing import Any

from t2s.tools.base import RETRY_HINT, Tool, ToolContext, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self) -> list[dict]:
        """按名称稳定排序（对 prompt cache 友好，nanobot 惯例）。"""
        return [self._tools[n].to_schema() for n in sorted(self._tools)]

    def _suggest_from(self, unknown: str) -> str | None:
        matches = difflib.get_close_matches(unknown.lower(), [n.lower() for n in self._tools], n=1, cutoff=0.6)
        if not matches:
            return None
        for n in self._tools:
            if n.lower() == matches[0]:
                return n
        return None

    def execute(self, name: str, arguments: Any, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            suggestion = self._suggest_from(name)
            hint = f"你是不是想用 '{suggestion}'？" if suggestion else ""
            return ToolResult.error(f"未知工具 '{name}'。可用工具: {', '.join(self.names())}。{hint}")
        args = self._normalize_args(name, arguments)
        if isinstance(args, ToolResult):
            return args
        params, err = tool.cast_and_validate(args)
        if err:
            return ToolResult.error(f"工具 {name} {err}")
        try:
            return tool.execute(ctx, **params)
        except Exception as e:  # noqa: BLE001 —— 错误即观察，不允许逃逸到引擎
            return ToolResult.error(f"工具 {name} 执行异常: {type(e).__name__}: {e}{RETRY_HINT}")

    @staticmethod
    def _normalize_args(name: str, arguments: Any) -> Any:
        """兼容三种形态：JSON 字符串 / {"arguments": {...}} 包装 / 直接 dict。"""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as e:
                return ToolResult.error(f"工具 {name} 的参数不是合法 JSON: {e}")
        if isinstance(arguments, dict) and set(arguments) == {"arguments"} and isinstance(arguments["arguments"], dict):
            arguments = arguments["arguments"]
        return arguments
