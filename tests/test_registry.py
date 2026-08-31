"""工具契约与注册中心行为。"""
from __future__ import annotations

import pytest

from t2s.tools import Tool, ToolRegistry, ToolResult, build_registry
from t2s.tools.base import RETRY_HINT


class Boom(Tool):
    name = "boom"
    description = "总是抛异常的工具"
    parameters = {"type": "object", "additionalProperties": False, "properties": {}}

    def execute(self, ctx, **params) -> ToolResult:
        raise RuntimeError("内部爆炸")


def test_definitions_stable_sorted():
    names = [d["function"]["name"] for d in build_registry().definitions()]
    assert names == sorted(names) == ["ask_user", "execute_sql", "get_schema", "search_schema", "validate_sql"]


def test_duplicate_register_raises():
    r = build_registry()
    with pytest.raises(ValueError, match="重名"):
        r.register(build_registry().get("get_schema"))


def test_unknown_tool_gives_suggestion(ctx):
    r = build_registry()
    out = r.execute("get_scheam", {"table": "customers"}, ctx)
    assert out.is_error and "get_schema" in out


def test_json_string_args_and_cast(ctx, registry):
    out = registry.execute("search_schema", '{"query": "客户风险等级", "top_k": "2"}', ctx)
    assert not out.is_error


def test_wrapper_arguments_unwrapped(ctx, registry):
    out = registry.execute("validate_sql", {"arguments": {"sql": "SELECT 1"}}, ctx)
    assert not out.is_error and '"valid": true' in out


def test_unknown_param_rejected(ctx, registry):
    out = registry.execute("execute_sql", {"sql": "SELECT 1", "hack": True}, ctx)
    assert out.is_error and "hack" in out


def test_tool_exception_becomes_error_observation(ctx):
    r = ToolRegistry()
    r.register(Boom())
    out = r.execute("boom", {}, ctx)
    assert out.is_error and "内部爆炸" in out and out.endswith(RETRY_HINT)
