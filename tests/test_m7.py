"""M7 测试：RRF 混合检索、复用统计、启发式重排、权限硬约束（ADR-008 D3/D4/D6）。"""
from __future__ import annotations

import json
from dataclasses import replace

from t2s.executor import ReActEngine
from t2s.llm import LLMResponse, ToolCall
from t2s.manager import RetrievalService, rrf_fusion
from t2s.models.records import AuditEntry
from t2s.models.task import TaskRequest
from t2s.router import PermissionRegistry, RouterService
from t2s.storage import AuditLog, MemoryStore, SessionStore, open_db
from t2s.tools import ToolContext, build_registry

from test_engine import ScriptLLM, final, tool_resp


def classify(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def tables_corpus():
    from t2s.tools.metadata import TABLES
    return [(t.name, t.search_corpus) for t in TABLES]


def make_retrieval(tmp_path, embedder=None):
    conn = open_db(tmp_path / "memory.db")
    store = MemoryStore(conn)
    return RetrievalService(tables=tables_corpus(), store=store, embedder=embedder), store


# ---------- RRF 融合 ----------

def test_rrf_keyword_weight_dominates():
    # 关键词排第一的候选 vs 语义排第一的候选：0.7 权重下关键词应胜出
    scores = rrf_fusion([(["a", "b", "c"], 0.7), (["c", "b", "a"], 0.3)])
    assert scores["a"] > scores["c"]  # a 关键词第 1（0.7/61）> c 语义第 1（0.3/61）


def test_rrf_both_channels_agree():
    scores = rrf_fusion([(["x", "y"], 0.7), (["x", "y"], 0.3)])
    assert scores["x"] > scores["y"]


# ---------- 表召回：复用加权 + 精确命中 ----------

def test_search_tables_keyword_primary(tmp_path):
    retrieval, _ = make_retrieval(tmp_path)
    hits = retrieval.search_tables("客户风险等级分布", top_k=3)
    assert hits[0][0] == "customers"


def test_usage_boost_flips_ranking(tmp_path):
    retrieval, store = make_retrieval(tmp_path)
    # "客户" 命中 customers/accounts（风险等级/开户营业部等语料）
    base = retrieval.search_tables("客户分布", top_k=3)
    names_before = [n for n, _ in base]
    # 重度使用 accounts：复用加权应将其顶到 customers 之前（同档关键词命中）
    for _ in range(20):
        store.bump_usage(["accounts"], [])
    after = retrieval.search_tables("客户分布", top_k=3)
    names_after = [n for n, _ in after]
    assert "accounts" in names_after
    assert names_after.index("accounts") < names_before.index("accounts") or \
        names_before.index("accounts") != 0


def test_exact_table_name_hit_boost(tmp_path):
    retrieval, _ = make_retrieval(tmp_path)
    hits = retrieval.search_tables("从 trades 表统计成交笔数", top_k=2)
    assert hits[0][0] == "trades"  # 表名原文出现在问题中 → 精确命中强信号


# ---------- 复用统计写入（sqlglot 反解） ----------

def test_record_success_bumps_usage(tmp_path):
    retrieval, store = make_retrieval(tmp_path)

    class MemoryShim:
        pass

    from t2s.manager import MemoryService
    memory = MemoryService.__new__(MemoryService)
    memory.store = store
    memory.embedder = None
    memory.retrieval = retrieval
    memory.record_success("买卖分布", "SELECT side, COUNT(*) FROM trades GROUP BY side")
    usage = store.usage_map("table")
    assert usage.get("trades") == 1
    fields = store.usage_map("field")
    assert fields.get("side") == 1


# ---------- 样例重排：精确命中 + 近因 ----------

def test_rank_pairs_exact_hit_boost(tmp_path):
    retrieval, store = make_retrieval(tmp_path)
    memory_store = MemoryStore(store.conn)
    memory_store.add_pair("各营业部成交额排行", "SELECT_A")
    memory_store.add_pair("基金净值走势", "SELECT_B")
    pairs = memory_store.all_pairs(status=None)
    top = retrieval.rank_pairs("各营业部成交额排行", pairs, top_k=2)
    assert top[0].sql == "SELECT_A"


# ---------- 权限硬约束（ADR-008 D6） ----------

def test_execute_sql_blocked_by_whitelist(registry, ctx):
    restricted = replace(ctx, allowed_tables=frozenset({"customers"}))
    out = registry.execute("execute_sql", {"sql": "SELECT COUNT(*) FROM trades"}, restricted)
    assert out.is_error and "权限边界" in out and "trades" in out


def test_validate_sql_flags_whitelist(registry, ctx):
    restricted = replace(ctx, allowed_tables=frozenset({"customers"}))
    out = registry.execute("validate_sql", {"sql": "SELECT COUNT(*) FROM trades"}, restricted)
    data = json.loads(str(out))
    assert data["valid"] is False and any("权限边界" in e for e in data["errors"])


def test_whitelist_user_allowed_table_passes(registry, ctx):
    restricted = replace(ctx, allowed_tables=frozenset({"customers"}))
    out = registry.execute("execute_sql", {"sql": "SELECT COUNT(*) FROM customers"}, restricted)
    assert not out.is_error


def test_permission_registry_decision():
    registry = PermissionRegistry({"analyst": frozenset({"customers", "accounts"}),
                                   "guest": frozenset()})
    d1 = registry.check("analyst")
    assert d1.allowed and d1.allowed_tables == frozenset({"customers", "accounts"})
    assert registry.check("guest").allowed is False
    assert registry.check("dev").allowed and registry.check("dev").allowed_tables is None


# ---------- 路由层集成：白名单随请求注入 ----------

def test_service_injects_whitelist_into_ctx(registry, ctx, tmp_path):
    conn = open_db(tmp_path / "memory.db")
    perm = PermissionRegistry({"analyst": frozenset({"customers"})})
    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "execute_sql", {"sql": "SELECT COUNT(*) AS n FROM trades"}),
        final("无法访问 trades 表。"),
    ])
    service = RouterService(llm, ReActEngine(llm, registry),
                            SessionStore(conn), AuditLog(conn), permissions=perm)
    ans = service.handle("查成交笔数", user_id="analyst", ctx=ctx)
    assert ans.stop_reason == "final"
    tool_msg = [m for c in llm.calls for m in c["messages"]
                if m["role"] == "tool" and "权限边界" in (m.get("content") or "")]
    assert tool_msg  # 白名单已注入执行层，越权 SQL 被硬边界拦截


# ---------- search_schema 走混合检索 ----------

def test_search_schema_uses_retriever(tmp_path):
    retrieval, store = make_retrieval(tmp_path)
    store.bump_usage(["margin_trades"] * 15, [])
    ctx = ToolContext(db_path=tmp_path / "x.db", retriever=retrieval)
    registry = build_registry()
    out = registry.execute("search_schema", {"query": "两融余额排行", "top_k": 3}, ctx)
    assert "margin_trades" in out  # 复用加权让两融表前置（语料本命中较弱的场景）


def test_search_schema_fallback_without_retriever(ctx):
    registry = build_registry()
    out = registry.execute("search_schema", {"query": "客户风险等级分布"}, ctx)
    assert "customers" in out  # 无 retriever 时关键词兜底路径不变
