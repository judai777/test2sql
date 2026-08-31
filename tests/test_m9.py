"""M9 测试：skill 目录化、MCP 往返、coder 委派（递归预算 + HIL 透传）（ADR-008 D1）。"""
from __future__ import annotations

from dataclasses import replace

from t2s.executor import ReActEngine
from t2s.executor.delegate import CoderDelegation, DelegateSqlTask
from t2s.executor.skill_registry import load_skills
from t2s.llm import LLMResponse, ToolCall
from t2s.models.task import Budget, TaskRequest
from t2s.storage import open_db
from t2s.tools import build_registry
from t2s.tools.mcp_bridge import build_mcp_backed_registry
from t2s.tools.mcp_server import build_db_mcp_server

from test_engine import ScriptLLM, final, tool_resp


def classify(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def tc(cid: str, name: str, args: dict) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args)


# ---------- skill 目录化 ----------

def test_table_directory_resolved_in_skill_prompt(registry, ctx):
    """data-analysis skill 的表目录占位符由引擎动态填充（单源=metadata.py）。"""
    llm = ScriptLLM([final("ok")])
    engine = ReActEngine(llm, registry,
                         system_prompt=load_skills()["data-analysis"].system_prompt)
    engine.run(TaskRequest(question="q"), ctx)
    system = llm.calls[0]["messages"][0]["content"]
    assert "{{table_directory}}" not in system      # 占位符已被填充
    assert "客户主档" in system and "成交流水" in system  # 目录来自 metadata 单源


def test_skills_loaded():
    skills = load_skills()
    assert set(skills) == {"data-analysis", "coder"}
    da, coder = skills["data-analysis"], skills["coder"]
    assert "delegate_sql_task" in da.tools          # 主 agent 可委派
    assert "search_schema" not in coder.tools       # coder 工具面收窄（无探索工具）
    assert coder.max_steps == 6                     # 子 agent 预算 profile
    assert "SQL 工程师" in coder.system_prompt


# ---------- MCP 往返（内存传输走完整协议） ----------

def test_mcp_roundtrip(registry, ctx):
    server = build_db_mcp_server(replace(ctx, allowed_tables=None))
    coder_registry = build_mcp_backed_registry(server)
    out = coder_registry.execute("execute_sql",
                                 {"sql": "SELECT COUNT(*) AS n FROM trades"}, ctx)
    assert not out.is_error and '"rows": [[8]]' in out  # 1 行结果，COUNT 值为 8


def test_mcp_roundtrip_error_semantics_preserved(registry, ctx):
    server = build_db_mcp_server(replace(ctx, allowed_tables=frozenset({"customers"})))
    coder_registry = build_mcp_backed_registry(server)
    out = coder_registry.execute("execute_sql", {"sql": "SELECT * FROM trades"}, ctx)
    assert out.is_error and "权限边界" in out  # 护栏语义跨协议保留


# ---------- coder 委派全流程 ----------

def _stack(registry, ctx, child_llm, coder_skill=None):
    skills = load_skills()
    server = build_db_mcp_server(replace(ctx, allowed_tables=None))
    coder_registry = build_mcp_backed_registry(server)
    coder_engine = ReActEngine(child_llm, coder_registry,
                               system_prompt=(coder_skill or skills["coder"]).system_prompt)
    delegation = CoderDelegation(coder_engine, server_ctx_for(server, ctx), skills["coder"])
    main_registry = build_registry()
    main_registry.register(DelegateSqlTask(delegation))
    return main_registry


def server_ctx_for(server, ctx):
    # 与 build_stack 一致：服务端 ctx 与委派共享（ask_user 运行时透传）
    return ctx


def test_delegation_full_flow(registry, ctx, tmp_path):
    child_llm = ScriptLLM([
        tool_resp("c1", "execute_sql", {"sql": "SELECT COUNT(*) AS n FROM trades"}),
        final("执行完成，共 8 笔。"),
    ])
    main_registry = _stack(registry, ctx, child_llm)
    parent_llm = ScriptLLM([
        classify('{"intent": "data_query"}') if False else
        tool_resp("p1", "delegate_sql_task",
                  {"task": "统计成交笔数", "schema_context": "trades(trade_id, side)"}),
        final("父结论：共 8 笔成交。"),
    ])
    engine = ReActEngine(parent_llm, main_registry)
    ans = engine.run(TaskRequest(question="成交笔数"), ctx)

    assert ans.stop_reason == "final" and "8 笔" in ans.content
    # coder 的系统提示词来自 skill（agent 身份）
    child_system = child_llm.calls[0]["messages"][0]["content"]
    assert "SQL 工程师" in child_system
    # 父循环收到的观察包含 coder 结果
    parent_tool = [m for c in parent_llm.calls for m in c["messages"]
                   if m["role"] == "tool" and m.get("tool_call_id") == "p1"]
    assert "[coder 执行结果]" in parent_tool[0]["content"] and "8 笔" in parent_tool[0]["content"]


def test_delegation_budget_refusal(registry, ctx):
    child_llm = ScriptLLM([])
    main_registry = _stack(registry, ctx, child_llm)
    parent_llm = ScriptLLM([
        tool_resp("p1", "delegate_sql_task", {"task": "统计"}),
        final("父预算不足，我直接作答。"),
    ])
    engine = ReActEngine(parent_llm, main_registry)
    ans = engine.run(TaskRequest(question="x", budget=Budget(max_steps=2)), ctx)
    tool_msg = [m for c in parent_llm.calls for m in c["messages"]
                if m["role"] == "tool" and m.get("tool_call_id") == "p1"]
    assert "预算不足" in tool_msg[0]["content"]      # 递归预算防护：父剩余步数不足即拒绝
    assert child_llm.calls == []                      # 子 agent 未被启动


def test_delegation_hil_passthrough(registry, ctx):
    asked = []
    def fake_ask(q, o):
        asked.append(q)
        return "用户答复：近30天"
    parent_ctx = replace(ctx, ask_user=fake_ask)
    child_llm = ScriptLLM([
        tool_resp("c1", "ask_user", {"question": "统计区间是什么？"}),
        final("已按近30天完成统计。"),
    ])
    main_registry = _stack(registry, parent_ctx, child_llm)
    parent_llm = ScriptLLM([
        tool_resp("p1", "delegate_sql_task", {"task": "近30天成交统计"}),
        final("近30天成交统计完成（human-in-the-loop 生效）。"),
    ])
    engine = ReActEngine(parent_llm, main_registry)
    ans = engine.run(TaskRequest(question="近30天成交"), parent_ctx)
    assert asked == ["统计区间是什么？"]          # coder 的澄清透传到了父 turn 用户通道
    child_tool = [m for c in child_llm.calls for m in c["messages"]
                  if m["role"] == "tool" and m.get("tool_call_id") == "c1"]
    assert "用户答复：近30天" in child_tool[0]["content"]
    assert "近30天" in ans.content
