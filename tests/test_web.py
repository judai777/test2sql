"""M6 Web 层测试：端点行为（真实服务栈，危险拦截路径零 LLM 即可测）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from t2s.config import AppConfig, ToolConfig
from t2s.models.task import Answer
from t2s.storage import open_db

from test_engine import ScriptLLM


def make_client(tmp_path):
    cfg = AppConfig(tools=ToolConfig(
        db_path=tmp_path / "biz.db", memory_db_path=tmp_path / "memory.db"))
    # 单元测试不外呼真实 LLM：无效端口 + 零重试 → data_query 走 error 分支（毫秒级）
    cfg.llm.base_url = "http://127.0.0.1:9"
    cfg.llm.retry_delays = (0.0, 0.0)
    cfg.embedding.model = ""
    # 借用 conftest 的迷你业务库结构：直接把 schema 建到 biz.db
    import sqlite3
    from pathlib import Path
    schema = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(cfg.tools.db_path)
    conn.executescript(schema)
    conn.execute("INSERT INTO branches VALUES (1,'测试营业部','北京','张三','2020-01-01')")
    conn.commit()
    conn.close()

    from t2s.ui.web import create_app
    app = create_app(cfg)
    return TestClient(app)


def test_index_serves_page(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200 and "Test2SQL" in r.text and "/api/ask" in r.text


def test_dangerous_blocked_without_llm(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/ask", json={"question": "帮我删掉所有客户", "session_id": "t1"})
    assert r.status_code == 200
    data = r.json()
    assert data["stop_reason"] == "blocked_dangerous" and "只读" in data["content"]


def test_ask_returns_answer_shape(tmp_path):
    client = make_client(tmp_path)
    # 未配置 LLM key 时 data_query 会走 error 分支——只断言响应结构与审计落库
    r = client.post("/api/ask", json={"question": "客户总数是多少", "session_id": "t2"})
    data = r.json()
    for key in ("content", "sql", "row_count", "result", "stop_reason", "steps",
                "total_tokens", "elapsed_ms"):
        assert key in data


def test_audit_endpoint(tmp_path):
    client = make_client(tmp_path)
    client.post("/api/ask", json={"question": "删除数据", "session_id": "t3"})
    r = client.get("/api/audit")
    assert r.status_code == 200
    entries = r.json()
    assert any(e["intent"] == "dangerous" for e in entries)


def test_answer_carries_result_sample(registry, ctx):
    """引擎捕获结果集样本供 UI 渲染（M6）。"""
    from t2s.executor import ReActEngine
    from t2s.llm import LLMResponse, ToolCall
    from t2s.models.task import TaskRequest

    llm = ScriptLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="execute_sql",
                                         arguments={"sql": "SELECT side, COUNT(*) AS n FROM trades GROUP BY side"})],
                    finish_reason="tool_calls"),
        LLMResponse(content="买卖各 4 笔。", finish_reason="stop"),
    ])
    ans = ReActEngine(llm, registry).run(TaskRequest(question="买卖分布"), ctx)
    assert ans.result is not None
    assert ans.result["columns"] == ["side", "n"] and len(ans.result["rows"]) == 2
