"""管理层：上下文装配与会话窗口裁剪（design.md §2 管理层职责，零 LLM）。"""
from __future__ import annotations

from t2s.llm import ChatMessage
from t2s.storage import SessionStore

CONTEXT_MAX_MESSAGES = 8   # 窗口：最近 4 轮（user+assistant 各一条）
CONTEXT_MAX_CHARS = 800    # 单条截断，防止单轮超长回答撑爆上下文


def build_history(store: SessionStore, session_id: str,
                  max_messages: int = CONTEXT_MAX_MESSAGES,
                  max_chars: int = CONTEXT_MAX_CHARS) -> list[ChatMessage]:
    """装配历史窗口：跨轮指代（'再按月分组'）依赖这里。

    M8：窗口外的历史经异步摘要滚动进 sessions.summary，以"[此前对话摘要]"
    系统消息前置——长会话不丢上下文（ADR-008 D2）。
    """
    summary, _ = store.get_summary(session_id)
    rows = store.window(session_id, limit=max_messages)
    history: list[ChatMessage] = []
    if summary:
        history.append(ChatMessage(role="system",
                                   content=f"[此前对话摘要] {summary}"))
    history.extend(ChatMessage(role=r.role, content=r.content[:max_chars]) for r in rows)
    return history
