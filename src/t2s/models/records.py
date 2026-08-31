"""存储层记录模型（会话消息 / 审计条目）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SessionMessage(BaseModel):
    """sessions 库中的单条消息（只存 user / assistant 轮次，工具明细留在引擎 trace）。"""

    id: int = 0
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    sql: str | None = None
    created_at: str = ""


class AuditEntry(BaseModel):
    """审计条目（PRD FR-12 / NFR-1）：每问必录，可复现。"""

    id: int = 0
    ts: str
    session_id: str = ""
    user_id: str = ""
    question: str
    intent: str = ""          # data_query | chat | dangerous
    stop_reason: str = ""
    sql: str | None = None
    row_count: int | None = None
    steps: int = 0
    total_tokens: int = 0
    elapsed_ms: int = 0
    content: str = ""         # 最终回答（截断存）

    @staticmethod
    def now_entry(question: str, **fields) -> "AuditEntry":
        """带当前时间戳的构造辅助。"""
        from datetime import datetime
        return AuditEntry(ts=datetime.now().isoformat(timespec="seconds"),
                          question=question, **fields)


class QAPair(BaseModel):
    """样例库条目：双通过的历史成功问答（few-shot 检索索引用）。

    M8 确认制：candidate（自动沉淀待确认）→ confirmed（用户确认进正式库）。
    """

    id: int = 0
    question: str
    sql: str
    embedding: list[float] | None = None
    status: str = "candidate"
    created_at: str = ""
    updated_at: str = ""


class MetricDoc(BaseModel):
    """口径库条目：业务指标口径文档（如"月活跃=月内有交易"）。"""

    id: int = 0
    title: str
    content: str
    embedding: list[float] | None = None
    created_at: str = ""
