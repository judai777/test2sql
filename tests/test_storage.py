"""存储层测试：会话窗口、审计落库。"""
from __future__ import annotations

from t2s.models.records import AuditEntry
from t2s.storage import AuditLog, SessionStore, open_db


def make_store(tmp_path):
    conn = open_db(tmp_path / "memory.db")
    return SessionStore(conn), AuditLog(conn)


def test_append_and_window_order(tmp_path):
    store, _ = make_store(tmp_path)
    store.append("s1", "user", "第一问")
    store.append("s1", "assistant", "第一答", sql="SELECT 1")
    store.append("s1", "user", "第二问")
    rows = store.window("s1")
    assert [r.content for r in rows] == ["第一问", "第一答", "第二问"]
    assert rows[1].sql == "SELECT 1"
    assert rows[0].role == "user"


def test_window_limit_returns_latest(tmp_path):
    store, _ = make_store(tmp_path)
    for i in range(12):
        store.append("s1", "user", f"消息{i}")
    rows = store.window("s1", limit=8)
    assert len(rows) == 8
    assert rows[0].content == "消息4"  # 最近的 8 条，且保持时间正序
    assert rows[-1].content == "消息11"


def test_sessions_are_isolated(tmp_path):
    store, _ = make_store(tmp_path)
    store.append("a", "user", "A的问题")
    store.append("b", "user", "B的问题")
    assert store.count("a") == 1 and store.count("b") == 1


def test_audit_roundtrip(tmp_path):
    store, audit = make_store(tmp_path)
    audit.log(AuditEntry.now_entry(
        question="各营业部成交额", session_id="s1", user_id="dev", intent="data_query",
        stop_reason="final", sql="SELECT 1", row_count=5, steps=3,
        total_tokens=120, elapsed_ms=900, content="答案是…"))
    entries = audit.recent()
    assert len(entries) == 1
    e = entries[0]
    assert e.intent == "data_query" and e.row_count == 5 and e.sql == "SELECT 1"
    assert e.ts  # 时间戳已记录
