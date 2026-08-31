"""工具层出口：注册中心构建与五件套导出。"""
from __future__ import annotations

from t2s.tools.ask_user import AskUser
from t2s.tools.base import Tool, ToolContext, ToolResult
from t2s.tools.registry import ToolRegistry
from t2s.tools.sql_tools import ExecuteSql, GetSchema, SearchSchema, ValidateSql


def build_registry() -> ToolRegistry:
    """注册五件套（M1 固定集合；M2 引擎直接消费 definitions()）。"""
    registry = ToolRegistry()
    for tool in (SearchSchema(), GetSchema(), ValidateSql(), ExecuteSql(), AskUser()):
        registry.register(tool)
    return registry


__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolRegistry",
    "build_registry",
    "SearchSchema",
    "GetSchema",
    "ValidateSql",
    "ExecuteSql",
    "AskUser",
]
