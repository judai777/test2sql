"""M5 评测体系测试。

离线部分（compare/summarize/runner 端到端）随常规 pytest 跑；
真实 LLM 冒烟标记 eval，仅在 .env 配置 key 时执行（pytest -m eval）。
"""
from __future__ import annotations

import json

import pytest

from eval.runner import (
    CaseResult,
    EvalCase,
    compare,
    judge_case,
    load_cases,
    _norm_row,
    run_eval,
    summarize,
)
from t2s.executor import ReActEngine
from t2s.llm import LLMResponse
from t2s.router import RouterService
from t2s.storage import AuditLog, SessionStore, open_db

from test_engine import ScriptLLM, final, tool_resp


def classify(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


# ---------- 比对与汇总 ----------

def test_multiset_compare():
    g_cols, g_rows = ["b", "v"], [("a", 1.0), ("b", 2.0)]
    assert compare(g_cols, g_rows, list(g_cols), [("b", 2.0), ("a", 1.0)], ordered=False)[0] is True
    ok, detail = compare(g_cols, g_rows, list(g_cols), [("a", 1.0)], ordered=False)
    assert ok is False and "1 行" in detail


def test_ordered_compare():
    g_cols, g_rows = ["b", "v"], [("a", 1.0), ("b", 2.0)]
    a_cols, a_rows = list(g_cols), [("a", 1.0), ("b", 2.0)]
    assert compare(g_cols, g_rows, a_cols, a_rows, ordered=True)[0] is True
    assert compare(g_cols, g_rows, a_cols, list(reversed(a_rows)), ordered=True)[0] is False


def test_column_projection_superset():
    # agent 多选了 extra 列、列序不同 → 按金标列名投影后应匹配
    ok, _ = compare(["name", "total"], [("北京", 21.65)],
                    ["total", "name", "extra"], [(21.65, "北京", 7)], ordered=False)
    assert ok is True


def test_scale_factor_unit_conversion():
    # agent 换算成亿元（÷1e8）→ 量纲因子下一致（≥2 行才有换算证据，见 CHG-008）
    ok, detail = compare(["name", "total"],
                         [("北京", 2512000000.0), ("上海", 2012000000.0)],
                         ["name", "total"],
                         [("北京", 25.12), ("上海", 20.12)], ordered=False)
    assert ok is True and "量纲" in detail


def test_scale_factor_rejects_single_row_arbitrary_ratio():
    # 单行 + 任意比例：无数换算证据，必须判负（防假阳性）
    ok, _ = compare(["n"], [(1548,)], ["n"], [(1341,)], ordered=False)
    assert ok is False


def test_float_tolerance():
    # AVG 未舍入 vs 金标 ROUND(2)：4 位内容差归一后一致
    assert compare(["v"], [(348.123456,)], ["v"], [(348.12,)], ordered=False)[0] is True


def test_fuzzy_type_alignment_superset_alias(registry=None):
    # A01 型：agent 列别名(customer_count) + 超集(percentage) → 类型签名对齐后匹配
    ok, _ = compare(["risk_level", "n"],
                    [("C1", 760), ("C2", 3011)],
                    ["risk_level", "customer_count", "percentage"],
                    [("C1", 760, 5.07), ("C2", 3011, 20.07)], ordered=True)
    assert ok is True


def test_real_mismatch_still_fails():
    ok, _ = compare(["name", "total"], [("北京", 2512000000.0)],
                    ["name", "total"], [("上海", 25.12)], ordered=False)
    assert ok is False


def test_normalize_bool_and_none():
    assert _norm_row((True, None)) == (1, None)


def test_summarize_math():
    results = [
        CaseResult(id="1", tier="single", question="a", passed=True, valid=True, steps=2, tokens=100),
        CaseResult(id="2", tier="single", question="b", passed=False, valid=True, steps=4, tokens=200),
        CaseResult(id="3", tier="join", question="c", passed=True, valid=True, steps=3, tokens=150),
    ]
    s = summarize(results)
    assert s["accuracy"] == round(2 / 3, 4)
    assert s["by_tier"]["single"]["accuracy"] == 0.5
    assert s["by_tier"]["join"]["accuracy"] == 1.0
    assert s["avg_steps"] == 3.0 and s["avg_tokens"] == 150


def test_load_golden_50():
    cases = load_cases()
    assert len(cases) == 50
    tiers = {c.tier for c in cases}
    assert tiers == {"single", "join", "agg", "time", "ambiguity"}
    assert sum(1 for c in cases if c.tier == "single") == 15
    assert sum(1 for c in cases if c.tier == "join") == 15
    assert sum(1 for c in cases if c.tier == "ambiguity") == 5


# ---------- runner 端到端（离线，ScriptLLM + 迷你库） ----------

def _service(llm, tmp_path):
    conn = open_db(tmp_path / "eval-memory.db")
    return RouterService(llm, ReActEngine(llm, _registry()), SessionStore(conn), AuditLog(conn))


def _registry():
    from t2s.tools import build_registry
    return build_registry()


def test_runner_offline_end_to_end(registry, ctx, tmp_path):
    cases = [
        EvalCase("O1", "single", "trades 多少条", "SELECT COUNT(*) FROM trades", "result_match"),
        EvalCase("O2", "single", "C4 客户数", "SELECT COUNT(*) FROM customers WHERE risk_level = 'C4'", "result_match"),
        EvalCase("O3", "single", "委托总数", "SELECT COUNT(*) FROM orders", "result_match"),
    ]
    llm = ScriptLLM([
        # O1：正确（迷你库 trades 8 行）
        classify('{"intent": "data_query"}'),
        tool_resp("1", "execute_sql", {"sql": "SELECT COUNT(*) FROM trades"}),
        final("8 条。"),
        # O2：正确（迷你库无 C4 → 0）
        classify('{"intent": "data_query"}'),
        tool_resp("2", "execute_sql", {"sql": "SELECT COUNT(*) FROM customers WHERE risk_level = 'C4'"}),
        final("0 个（迷你库没有 C4）。"),
        # O3：答错表（customers 2 行 ≠ orders 0 行）
        classify('{"intent": "data_query"}'),
        tool_resp("3", "execute_sql", {"sql": "SELECT COUNT(*) FROM customers"}),
        final("答错表。"),
    ])
    service = _service(llm, tmp_path)
    judge_conn = __import__("sqlite3").connect(ctx.db_path)
    results = run_eval(cases, service, ctx, judge_conn)
    s = summarize(results)
    assert s["passed"] == 2 and s["accuracy"] == round(2 / 3, 4)
    assert s["by_tier"]["single"]["total"] == 3


def test_judge_valid_sql_tier(registry, ctx, tmp_path):
    case = EvalCase("X9", "ambiguity", "大户有哪些", None, "valid_sql")
    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "execute_sql", {"sql": "SELECT account_id FROM accounts WHERE market_value > 1000000"}),
        final("按市值假设的大户列表。"),
    ])
    import sqlite3
    conn = sqlite3.connect(ctx.db_path)
    answer = _service(llm, tmp_path) and None  # 占位：直接走 judge_case
    ans = type("Ans", (), {"sql": "SELECT account_id FROM accounts WHERE market_value > 1000000",
                           "stop_reason": "final", "steps": [], "total_tokens": 0,
                           "elapsed_ms": 0, "content": "假设市值百万"})()
    r = judge_case(case, ans, conn)
    assert r.passed and r.valid


def test_judge_no_sql_with_clarification_counts_for_ambiguity():
    case = EvalCase("X8", "ambiguity", "表现最好", None, "valid_sql")
    ans = type("Ans", (), {"sql": None, "stop_reason": "final",
                           "steps": [], "total_tokens": 0, "elapsed_ms": 0,
                           "content": "请问'表现'的定义是什么？"})()
    import sqlite3
    r = judge_case(case, ans, sqlite3.connect(":memory:"))
    assert r.passed and "澄清" in r.detail


# ---------- 真实 LLM 冒烟（pytest -m eval；无 key 自动跳过） ----------

def _has_key() -> bool:
    from t2s.config import AppConfig
    return bool(AppConfig.load().llm.api_key)


@pytest.mark.eval
def test_real_eval_smoke():
    # 真实调用计费：仅显式设置 T2S_REAL_EVAL=1 时执行
    import os
    if os.environ.get("T2S_REAL_EVAL") != "1":
        pytest.skip("未设置 T2S_REAL_EVAL=1，跳过真实评测（避免日常 pytest 意外计费）")
    from t2s.config import AppConfig
    from eval.runner import build_real_service, connect_ro
    cfg = AppConfig.load()
    if not cfg.llm.api_key:
        pytest.skip("未配置 T2S_LLM_API_KEY")
    service, ctx, memory_store = build_real_service(cfg, use_memory=True)
    cases = [c for c in load_cases() if c.tier == "single"][:3]
    conn = connect_ro(cfg.tools.db_path)
    results = run_eval(cases, service, ctx, conn, memory_store=memory_store)
    report = summarize(results)
    assert report["total"] == 3
    assert set(report["by_tier"].keys()) == {"single"}
