"""ReAct 引擎行为测试：ScriptLLM 按脚本回放，零网络依赖。

覆盖：正常终止、先声明后回填不变量、步数耗尽强制总结、打转警告/强停、
错误回喂诚实失败、墙钟、拒绝不执行、用量累计。
"""
from __future__ import annotations

import pytest
from t2s.llm import LLMResponse, ToolCall, Usage
from t2s.executor import ReActEngine
from t2s.models.task import Budget, TaskRequest


def tc(cid: str, name: str, args: dict) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args)


def tool_resp(cid: str, name: str, args: dict, usage: Usage | None = None) -> LLMResponse:
    return LLMResponse(tool_calls=[tc(cid, name, args)], finish_reason="tool_calls", usage=usage or Usage())


def final(text: str, usage: Usage | None = None) -> LLMResponse:
    return LLMResponse(content=text, finish_reason="stop", usage=usage or Usage())


class ScriptLLM:
    """按脚本回放的假 LLM：记录每次收到的 messages 与 tools。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, temperature=None) -> LLMResponse:
        self.calls.append({"messages": [m.model_dump() for m in messages], "tools": tools})
        if not self.responses:
            raise AssertionError("LLM 脚本已耗尽（引擎发起了脚本之外的调用）")
        return self.responses.pop(0)


def task(**kw) -> TaskRequest:
    return TaskRequest(question="测试问题", **kw)


def tool_msgs(llm: ScriptLLM, cid: str | None = None) -> list[dict]:
    out = []
    for c in llm.calls:
        for m in c["messages"]:
            if m["role"] == "tool" and (cid is None or m.get("tool_call_id") == cid):
                out.append(m)
    return out


def test_happy_path(registry, ctx):
    sql = "SELECT side, COUNT(*) AS n FROM trades GROUP BY side"
    llm = ScriptLLM([
        tool_resp("1", "get_schema", {"table": "trades"}),
        tool_resp("2", "execute_sql", {"sql": sql}),
        final("买入 4 笔，卖出 4 笔。"),
    ])
    ans = ReActEngine(llm, registry).run(task(), ctx)
    assert ans.stop_reason == "final" and "买入" in ans.content
    assert ans.sql == sql
    assert len(ans.steps) >= 3  # 2 次 llm + 2 次工具 + final


def test_invariant_declare_before_observation(registry, ctx):
    llm = ScriptLLM([
        tool_resp("1", "get_schema", {"table": "trades"}),
        tool_resp("2", "execute_sql", {"sql": "SELECT COUNT(*) FROM trades"}),
        final("共 8 笔。"),
    ])
    ReActEngine(llm, registry).run(task(), ctx)
    msgs = llm.calls[1]["messages"]
    idx_assistant = next(i for i, m in enumerate(msgs) if m["role"] == "assistant" and m["tool_calls"])
    idx_tool = next(i for i, m in enumerate(msgs) if m["role"] == "tool" and m.get("tool_call_id") == "1")
    assert idx_assistant < idx_tool  # 先声明后回填


def test_max_steps_forces_summary(registry, ctx):
    llm = ScriptLLM([
        tool_resp("1", "search_schema", {"query": "客户"}),
        tool_resp("2", "search_schema", {"query": "账户"}),
        final("总结：已定位客户与账户两张表。"),
    ])
    ans = ReActEngine(llm, registry).run(task(budget=Budget(max_steps=2)), ctx)
    assert ans.stop_reason == "max_steps_summary" and "总结" in ans.content
    last = llm.calls[-1]
    assert last["tools"] is None  # 无工具总结
    assert "预算" in last["messages"][-1]["content"]


def test_repeat_warns_and_skips_execution(registry, ctx):
    llm = ScriptLLM([
        tool_resp("1", "get_schema", {"table": "trades"}),
        tool_resp("2", "get_schema", {"table": "trades"}),  # 连续第 2 次 → warn，不执行
        final("明白，我换个思路。"),
    ])
    ans = ReActEngine(llm, registry).run(task(), ctx)
    assert ans.stop_reason == "final"
    m2 = next(m for m in tool_msgs(llm, "2"))
    assert "防护警告" in m2["content"]


def test_repeat_stop_forces_summary(registry, ctx):
    llm = ScriptLLM([
        tool_resp("1", "get_schema", {"table": "trades"}),
        tool_resp("2", "get_schema", {"table": "trades"}),
        tool_resp("3", "get_schema", {"table": "trades"}),  # 累计第 3 次 → 强停
        final("打转被拦截后的总结。"),
    ])
    ans = ReActEngine(llm, registry).run(task(budget=Budget(max_steps=8)), ctx)
    assert ans.stop_reason == "repeat_stop" and "总结" in ans.content
    # 第 3 次未真正执行，但按协议补了终止观察
    assert "防护终止" in tool_msgs(llm, "3")[0]["content"]


def test_repair_limit_honest_failure(registry, ctx):
    bad = "SELECT nocol FROM trades"
    llm = ScriptLLM([
        tool_resp("1", "execute_sql", {"sql": bad}),
        tool_resp("2", "search_schema", {"query": "客户"}),
        tool_resp("3", "execute_sql", {"sql": bad}),
        tool_resp("4", "search_schema", {"query": "账户"}),
        tool_resp("5", "execute_sql", {"sql": bad}),  # 第 3 次同 SQL 报错 → 诚实失败
    ])
    ans = ReActEngine(llm, registry).run(task(), ctx)
    assert ans.stop_reason == "repair_limit"
    assert "无法完成" in ans.content and "转人工" in ans.content
    assert ans.sql == bad


def test_wall_clock(registry, ctx):
    llm = ScriptLLM([final("时间耗尽前的总结。")])
    ans = ReActEngine(llm, registry).run(task(budget=Budget(turn_timeout_s=0.0)), ctx)
    assert ans.stop_reason == "wall_clock_summary" and "总结" in ans.content


def test_refusal_with_tool_calls_not_executed(registry, ctx):
    resp = LLMResponse(content="抱歉，我无法协助该请求。",
                       tool_calls=[tc("1", "execute_sql", {"sql": "SELECT 1"})],
                       finish_reason="content_filter")
    llm = ScriptLLM([resp])
    ans = ReActEngine(llm, registry).run(task(), ctx)
    assert ans.stop_reason == "final" and "无法协助" in ans.content
    assert len(llm.calls) == 1  # 没有第二轮：工具未执行


def test_usage_accumulated(registry, ctx):
    u = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    llm = ScriptLLM([
        tool_resp("1", "get_schema", {"table": "trades"}, usage=u),
        final("完成。", usage=u),
    ])
    ans = ReActEngine(llm, registry).run(task(), ctx)
    assert ans.total_tokens == 30 and ans.prompt_tokens == 20
