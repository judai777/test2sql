"""路由层测试：危险拦截、意图分流、会话装配、审计必录（PRD FR-6/7/12）。"""
from __future__ import annotations

import json

from t2s.executor import ReActEngine
from t2s.llm import LLMResponse, Usage
from t2s.router import RouterService
from t2s.storage import AuditLog, SessionStore, open_db

from test_engine import ScriptLLM, final, tool_resp


def classify(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def make_service(llm, tmp_path):
    conn = open_db(tmp_path / "memory.db")
    engine = ReActEngine(llm, _registry())
    return RouterService(llm, engine, SessionStore(conn), AuditLog(conn)), SessionStore(conn), AuditLog(conn)


def _registry():
    from t2s.tools import build_registry
    return build_registry()


def test_dangerous_blocked_without_llm(registry, ctx, tmp_path):
    llm = ScriptLLM([])  # 一次 LLM 调用都不该发生
    service, store, audit = make_service(llm, tmp_path)
    ans = service.handle("帮我删掉这只股票的持仓记录", ctx=ctx)
    assert ans.stop_reason == "blocked_dangerous" and "只读" in ans.content
    assert llm.calls == []
    assert audit.recent()[0].intent == "dangerous"  # 拦截也要留痕


def test_chat_intent_direct_answer(registry, ctx, tmp_path):
    llm = ScriptLLM([classify('{"intent": "chat"}'), final("你好！我是取数助手。")])
    service, store, audit = make_service(llm, tmp_path)
    ans = service.handle("你好", ctx=ctx)
    assert ans.stop_reason == "final" and "取数助手" in ans.content
    assert len(llm.calls) == 2  # 分类 + 直答，引擎未被调用
    assert audit.recent()[0].intent == "chat"
    assert store.count("default") == 0  # 闲聊不进会话历史（MVP 约定）


def test_data_query_full_pipeline(registry, ctx, tmp_path):
    sql = "SELECT side, COUNT(*) AS n FROM trades GROUP BY side"
    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "get_schema", {"table": "trades"}),
        tool_resp("2", "execute_sql", {"sql": sql}),
        final("买入 4 笔，卖出 4 笔。"),
    ])
    service, store, audit = make_service(llm, tmp_path)
    ans = service.handle("买卖各多少笔", ctx=ctx)
    assert ans.stop_reason == "final" and "买入" in ans.content
    assert ans.row_count == 2  # 引擎从 execute_sql 结果捕获
    # 会话：user + assistant 各一条
    assert store.count("default") == 2
    assert store.window("default")[-1].sql == sql
    # 审计：意图/SQL/行数齐全
    e = audit.recent()[0]
    assert e.intent == "data_query" and e.row_count == 2 and e.sql == sql


def test_history_injected_into_engine(registry, ctx, tmp_path):
    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "search_schema", {"query": "客户"}),
        final("历史注入验证。"),
    ])
    service, store, _ = make_service(llm, tmp_path)
    store.append("default", "user", "上一轮的问题：客户风险等级分布")
    store.append("default", "assistant", "上一轮的回答：C3 占比 40%。")
    service.handle("再按城市拆分", ctx=ctx)
    engine_msgs = llm.calls[1]["messages"]  # calls[0] 是意图分类
    joined = json.dumps(engine_msgs, ensure_ascii=False)
    assert "C3 占比 40%" in joined  # 引擎首条 LLM 请求已携带历史


def test_context_window_truncation(registry, ctx, tmp_path):
    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "search_schema", {"query": "持仓"}),
        final("窗口裁剪验证。"),
    ])
    service, store, _ = make_service(llm, tmp_path)
    for i in range(12):
        store.append("default", "user", f"长消息{i}" * 200)
    service.handle("汇总一下", ctx=ctx)
    engine_msgs = llm.calls[1]["messages"]
    # system + 窗口(≤8) + user
    assert len(engine_msgs) <= 10
    for m in engine_msgs[1:]:  # system 含表目录（设计使然），历史截断规则只管其余消息
        assert len(m["content"] or "") <= 800


def test_intent_fallback_on_garbage(registry, ctx, tmp_path):
    """分类器输出不合法时回退 data_query（主路径优先）。"""
    llm = ScriptLLM([classify("我觉得是闲聊吧（没有 JSON）"), final("答。")])
    service, _, audit = make_service(llm, tmp_path)
    service.handle("随便说点", ctx=ctx)
    assert audit.recent()[0].intent == "data_query"


def test_usage_token_zero_default(registry, ctx, tmp_path):
    llm = ScriptLLM([classify('{"intent": "chat"}'), final("嗨", usage=Usage(total_tokens=7))])
    service, _, audit = make_service(llm, tmp_path)
    service.handle("hi", ctx=ctx)
    # chat 直答路径暂不计费（MVP 约定，CHG-004 已注明）
    assert audit.recent()[0].total_tokens == 0
