"""审计日志（PRD FR-12）：谁、何时、问了什么、执行了什么 SQL、返回多少行。"""
from __future__ import annotations

import sqlite3

from t2s.models.records import AuditEntry

_CONTENT_MAX_CHARS = 500


class AuditLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def log(self, entry: AuditEntry) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (ts, session_id, user_id, question, intent, stop_reason,"
            " sql, row_count, steps, total_tokens, elapsed_ms, content)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (entry.ts, entry.session_id, entry.user_id, entry.question, entry.intent,
             entry.stop_reason, entry.sql, entry.row_count, entry.steps,
             entry.total_tokens, entry.elapsed_ms, entry.content[:_CONTENT_MAX_CHARS]),
        )
        self.conn.commit()

    def recent(self, limit: int = 20) -> list[AuditEntry]:
        rows = self.conn.execute(
            "SELECT id, ts, session_id, user_id, question, intent, stop_reason,"
            " sql, row_count, steps, total_tokens, elapsed_ms, content"
            " FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [AuditEntry(id=r[0], ts=r[1], session_id=r[2], user_id=r[3], question=r[4],
                           intent=r[5], stop_reason=r[6], sql=r[7], row_count=r[8],
                           steps=r[9], total_tokens=r[10], elapsed_ms=r[11], content=r[12])
                for r in rows]
