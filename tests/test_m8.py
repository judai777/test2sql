"""M8 测试：确认制记忆、结果表格记忆、异步摘要（ADR-008 D2）。"""
from __future__ import annotations

from t2s.executor import ReActEngine
from t2s.llm import LLMResponse
from t2s.manager import MemoryService, RetrievalService, SessionSummarizer
from t2s.models.task import TaskRequest
from t2s.router import RouterService
from t2s.storage import AuditLog, MemoryStore, SessionStore, open_db

from test_engine import ScriptLLM, final, tool_resp


def classify(content: str) -> LLMResponse:
    return LLMResponse(content=content, finish_reason="stop")


def make_memory(tmp_path):
    conn = open_db(tmp_path / "memory.db")
    store = MemoryStore(conn)
    retrieval = RetrievalService(tables=[("t", "x")], store=store, embedder=None)
    return MemoryService(store, None, retrieval=retrieval), conn


# ---------- 确认制 ----------

def test_candidate_not_visible_until_confirmed(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.record_success("客户总数", "SELECT COUNT(*) FROM customers")  # 自动进候选池
    assert memory.retrieve_few_shot("客户总数") == ""            # 候选不可检索
    pair_id = memory.store.candidate_pairs()[0].id
    assert memory.confirm_pair(pair_id) is True
    assert "SELECT COUNT(*) FROM customers" in memory.retrieve_few_shot("客户总数")


def test_seed_can_skip_confirmation(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.store.add_pair("营业部数量", "SELECT COUNT(*) FROM branches", status="confirmed")
    assert "branches" in memory.retrieve_few_shot("营业部数量")


def test_confirmed_never_downgraded_by_re_record(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.record_success("客户总数", "SELECT 1")
    memory.confirm_pair(memory.store.candidate_pairs()[0].id)
    memory.record_success("客户总数", "SELECT COUNT(*) FROM customers")  # 重跑更新
    pairs = memory.store.all_pairs(status=None)
    assert pairs[0].status == "confirmed" and "COUNT" in pairs[0].sql


# ---------- 结果表格记忆 ----------

def test_save_and_retrieve_result(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.save_result("8月成交排行", "8月各营业部成交额排行", "SELECT ...",
                       ["branch_name", "yi"], [["北京望京", 25.12]])
    out = memory.retrieve_saved_results("上个月成交额排行那张表")
    assert "8月成交排行" in out and "SELECT ..." in out


def test_save_result_no_hit(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.save_result("8月成交排行", "8月成交额", "SELECT 1", ["a"], [[1]])
    assert memory.retrieve_saved_results("基金净值") == ""


def test_saved_result_in_memory_context(tmp_path):
    memory, _ = make_memory(tmp_path)
    memory.save_result("8月成交排行", "8月各营业部成交额", "SELECT ...", ["a"], [[1]])
    ctx = memory.build_memory_context("8月各营业部成交额")
    assert "用户保存的历史结果" in ctx


# ---------- 异步摘要 ----------

def _fill_session(conn, session_id, n=12):
    store = SessionStore(conn)
    for i in range(n):
        store.append(session_id, "user", f"第{i}问：成交量相关的问题{i}")
        store.append(session_id, "assistant", f"第{i}答：结论{i}")
    return store


def test_summarizer_rolls_window_tail(tmp_path):
    conn = open_db(tmp_path / "memory.db")
    store = _fill_session(conn, "s1", n=6)  # 12 条消息
    llm = ScriptLLM([final("用户此前持续询问成交量，口径为 trade_date 近一月。")])
    summarizer = SessionSummarizer(llm, store, window=8, async_mode=False)
    summary = summarizer.summarize("s1")
    assert summary and "成交量" in summary
    text, until = store.get_summary("s1")
    assert until > 0
    # 水位之后只剩窗口内 8 条
    assert store.count_since("s1", until) == 8


def test_summarizer_skips_when_within_window(tmp_path):
    conn = open_db(tmp_path / "memory.db")
    store = _fill_session(conn, "s2", n=3)  # 6 条 ≤ 8
    llm = ScriptLLM([])
    summarizer = SessionSummarizer(llm, store, window=8, async_mode=False)
    assert summarizer.summarize("s2") is None
    assert llm.calls == []


def test_summary_injected_into_history(tmp_path):
    conn = open_db(tmp_path / "memory.db")
    store = _fill_session(conn, "s3", n=6)
    store.save_summary("s3", "用户此前询问成交量趋势。", until_id=4)
    from t2s.manager import build_history
    history = build_history(store, "s3")
    assert any("[此前对话摘要]" in m.content for m in history)
    assert history[0].role == "system"  # 摘要消息前置


def test_service_triggers_summarizer(registry, ctx, tmp_path):
    conn = open_db(tmp_path / "memory.db")
    memory_store = MemoryStore(conn)

    class RecordingSummarizer:
        def __init__(self):
            self.calls = []
        def maybe_summarize_async(self, session_id):
            self.calls.append(session_id)

    llm = ScriptLLM([
        classify('{"intent": "data_query"}'),
        tool_resp("1", "execute_sql", {"sql": "SELECT COUNT(*) FROM trades"}),
        final("8 笔。"),
    ])
    summarizer = RecordingSummarizer()
    service = RouterService(llm, ReActEngine(llm, registry), SessionStore(conn),
                            AuditLog(conn), summarizer=summarizer)
    service.handle("成交笔数", ctx=ctx)
    assert summarizer.calls == ["default"]
