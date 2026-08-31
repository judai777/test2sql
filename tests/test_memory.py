"""记忆三层测试：样例库沉淀/检索/点踩、口径库、few-shot 注入（PRD FR-8）。"""
from __future__ import annotations

from t2s.executor import ReActEngine
from t2s.llm import LLMResponse
from t2s.manager import MemoryService
from t2s.models.task import TaskRequest
from t2s.router import RouterService
from t2s.storage import AuditLog, MemoryStore, SessionStore, open_db

from test_engine import ScriptLLM, final, tool_resp


def classify(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def make_memory(tmp_path, embedder=None):
    conn = open_db(tmp_path / "memory.db")
    return MemoryService(MemoryStore(conn), embedder), conn


class FakeEmbedder:
    """确定性嵌入：按特征字出现位置置 1，相似文本余弦自然更近。"""

    _CHARS = "营业部成交额客户风险等级持仓净值"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 if ch in t else 0.0 for ch in self._CHARS] for t in texts]


# ---------- 样例库（关键词降级通道） ----------

def test_record_and_keyword_retrieval(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.record_success("各营业部成交额排行", "SELECT b.branch_name, SUM(t.amount) FROM ...")
    memory.confirm_pair(memory.store.candidate_pairs()[0].id)  # 确认制：确认后才可检索
    out = memory.retrieve_few_shot("近一月各营业部的成交额排行")
    assert "营业部" in out and "SUM(t.amount)" in out


def test_no_hit_returns_empty(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.record_success("各营业部成交额排行", "SELECT 1")
    assert memory.retrieve_few_shot("基金净值走势") == ""


def test_empty_store_returns_empty(tmp_path):
    memory, _ = make_memory(tmp_path)
    assert memory.retrieve_few_shot("随便") == "" and memory.retrieve_metrics("随便") == ""


def test_forget_pair(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.record_success("客户总数", "SELECT COUNT(*) FROM customers")
    pair_id = memory.store.all_pairs(status=None)[0].id
    assert memory.forget_pair(pair_id) is True        # 点踩删除
    assert memory.retrieve_few_shot("客户总数") == ""


def test_upsert_same_question(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.record_success("客户总数", "SELECT 1")
    memory.record_success("客户总数", "SELECT COUNT(*) FROM customers")
    pairs = memory.store.all_pairs(status=None)
    assert len(pairs) == 1 and "COUNT" in pairs[0].sql  # 幂等更新而非堆叠


# ---------- 语义通道（FakeEmbedder） ----------

def test_embedding_ranking(tmp_path):
    memory, _ = make_memory(tmp_path, embedder=FakeEmbedder())
    memory.record_success("各营业部成交额排行", "SQL_A")
    memory.record_success("C4 级客户有多少", "SQL_B")
    for p in memory.store.candidate_pairs():
        memory.confirm_pair(p.id)
    out = memory.retrieve_few_shot("营业部成交额")
    assert out.index("SQL_A") < out.index("SQL_B")  # 余弦排序命中语义近邻


def test_embedder_failure_falls_back(tmp_path):
    class BrokenEmbedder:
        def embed(self, texts):
            raise RuntimeError("网络炸了")

    memory, _ = make_memory(tmp_path, embedder=BrokenEmbedder())
    memory.record_success("客户风险等级", "SELECT 1")   # 写入时向量化失败 → 存 NULL
    memory.confirm_pair(memory.store.candidate_pairs()[0].id)
    assert memory.store.all_pairs()[0].embedding is None
    assert "SELECT 1" in memory.retrieve_few_shot("客户风险等级分布")  # 关键词降级仍可命中


# ---------- 口径库 ----------

def test_metrics_retrieval(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.store.add_metric("月活跃客户", "月活跃 = 自然月内有过成交的客户数")
    out = memory.retrieve_metrics("月活跃客户有多少")
    assert "月内有过成交" in out


def test_build_memory_context_merges(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.record_success("各营业部成交额排行", "SELECT ...")
    memory.confirm_pair(memory.store.candidate_pairs()[0].id)
    memory.store.add_metric("月活跃客户", "月活跃 = 月内有交易")
    ctx = memory.build_memory_context("各营业部成交额排行，只看月活跃客户")
    assert "相似历史问答" in ctx and "业务口径" in ctx


# ---------- 引擎与路由集成 ----------

def test_engine_injects_few_shot_into_system(registry, ctx):
    llm = ScriptLLM([final("ok")])
    ReActEngine(llm, registry).run(TaskRequest(question="q"), ctx,
                                   few_shot="### 相似历史问答\n问：x\nSQL：SELECT 1")
    system = llm.calls[0]["messages"][0]["content"]
    assert "SELECT 1" in system and "参考记忆" in system


def test_router_records_success_and_reuses(registry, ctx, tmp_path):
    memory, conn = make_memory(tmp_path)
    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "execute_sql", {"sql": "SELECT COUNT(*) FROM trades"}),
        final("共 8 笔。"),
        # 第二次相似提问
        classify('{"intent": "data_query"}'),
        final("复用记忆作答。"),
    ])
    service = RouterService(llm, ReActEngine(llm, registry),
                            SessionStore(conn), AuditLog(conn), memory=memory)
    ans1 = service.handle("买卖各多少笔", ctx=ctx)
    assert ans1.stop_reason == "final" and ans1.row_count == 1  # COUNT 查询返回 1 行
    assert len(memory.store.all_pairs(status=None)) == 1  # 自动沉淀进候选池
    for p in memory.store.candidate_pairs():
        memory.confirm_pair(p.id)  # 用户确认进正式库

    ans2 = service.handle("买卖各多少笔统计一下", ctx=ctx)
    assert ans2.stop_reason == "final"
    engine_system = llm.calls[4]["messages"][0]["content"]  # 0分类 1引擎 2引擎收尾 3分类2 4第二次引擎
    assert "SELECT COUNT(*) FROM trades" in engine_system  # few-shot 已注入
    assert audit_has(service, "买卖各多少笔")


def audit_has(service: RouterService, question: str) -> bool:
    return any(e.question == question for e in service.audit.recent(50))


def test_audit_entry_shape(registry, ctx, tmp_path):
    memory, conn = make_memory(tmp_path)
    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "execute_sql", {"sql": "SELECT side FROM trades LIMIT 2"}),
        final("两条。"),
    ])
    service = RouterService(llm, ReActEngine(llm, registry),
                            SessionStore(conn), AuditLog(conn), memory=memory)
    service.handle("看两笔成交", ctx=ctx)
    e = service.audit.recent()[0]
    assert e.intent == "data_query" and e.row_count == 2 and e.sql is not None
