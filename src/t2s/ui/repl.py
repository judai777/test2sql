"""REPL 驱动器（M3 多轮会话版）。ADR-005：本层零业务逻辑，只做 IO 与渲染。

用法:
    PYTHONPATH=src python -m t2s.ui.repl                 # 交互模式（多轮会话）
    PYTHONPATH=src python -m t2s.ui.repl -q "你的问题"    # 一次性模式
"""
from __future__ import annotations

import argparse
import sys

from t2s.config import AppConfig
from t2s.executor import ReActEngine
from t2s.executor.delegate import CoderDelegation, DelegateSqlTask
from t2s.executor.skill_registry import load_skills
from t2s.llm import EmbeddingClient, LLMClient
from t2s.manager import MemoryService, RetrievalService, SessionSummarizer
from t2s.router import RouterService
from t2s.storage import AuditLog, MemoryStore, SessionStore, open_db
from t2s.tools import ToolContext, build_registry
from t2s.tools.mcp_bridge import build_mcp_backed_registry
from t2s.tools.mcp_server import build_db_mcp_server
from t2s.tools.metadata import TABLES

_VERBOSE_EVENTS = ("tool_result", "guard", "refusal")


def ask_user(question: str, options: list[str] | None) -> str:
    print(f"\n❓ 澄清：{question}")
    if options:
        for i, opt in enumerate(options, 1):
            print(f"   {i}. {opt}")
    return input("✍️  你的回答: ").strip()


def on_event(event: str, data: dict) -> None:
    if event in _VERBOSE_EVENTS:
        print(f"  · [{event}] {data}")


def render(answer) -> None:
    print(f"\n{'=' * 60}\n🤖 {answer.content}\n{'-' * 60}")
    print(f"   stop={answer.stop_reason}  steps={len(answer.steps)}  "
          f"tokens={answer.total_tokens}  {answer.elapsed_ms}ms")
    if answer.sql:
        print(f"   SQL: {answer.sql}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Test2SQL REPL")
    ap.add_argument("-q", "--question", help="一次性提问（不进入交互循环）")
    args = ap.parse_args()

    cfg = AppConfig.load()
    if not cfg.llm.api_key:
        print("未配置 T2S_LLM_API_KEY：复制 .env.example 为 .env 并填入真实 key。")
        return 1

    llm = LLMClient(cfg.llm)
    skills = load_skills()
    engine_event = on_event
    conn = open_db(cfg.tools.memory_db_path)
    embedder = EmbeddingClient(cfg.embedding) if cfg.embedding.enabled else None
    store = MemoryStore(conn)
    retrieval = RetrievalService(tables=[(t.name, t.search_corpus) for t in TABLES],
                                 store=store, embedder=embedder)
    memory = MemoryService(store, embedder, retrieval=retrieval)

    server_ctx = ToolContext(db_path=cfg.tools.db_path,
                             sql_timeout_s=cfg.tools.sql_timeout_s,
                             sql_row_limit=cfg.tools.sql_row_limit)
    db_server = build_db_mcp_server(server_ctx)
    coder_registry = build_mcp_backed_registry(db_server)
    coder_engine = ReActEngine(llm, coder_registry,
                               system_prompt=skills["coder"].system_prompt)
    delegation = CoderDelegation(coder_engine, server_ctx, skills["coder"])

    registry = build_registry()
    registry.register(DelegateSqlTask(delegation))
    engine = ReActEngine(llm, registry, on_event=engine_event,
                         system_prompt=skills["data-analysis"].system_prompt)
    summarizer = SessionSummarizer(llm, SessionStore(conn), async_mode=True)
    service = RouterService(llm, engine, SessionStore(conn), AuditLog(conn),
                            memory=memory, summarizer=summarizer)
    ctx = ToolContext(
        db_path=cfg.tools.db_path,
        sql_timeout_s=cfg.tools.sql_timeout_s,
        sql_row_limit=cfg.tools.sql_row_limit,
        ask_user=ask_user,
        retriever=retrieval,
    )

    if args.question:
        render(service.handle(args.question, ctx=ctx))
        return 0

    print("Test2SQL REPL（多轮会话；输入 exit 退出）")
    while True:
        try:
            question = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in ("exit", "quit"):
            break
        render(service.handle(question, ctx=ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
