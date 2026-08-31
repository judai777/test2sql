"""会话存储：多轮对话的落盘与窗口读取。"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from t2s.models.records import SessionMessage


class SessionStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(self, session_id: str, role: str, content: str, sql: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO messages (session_id, role, content, sql, created_at) VALUES (?,?,?,?,?)",
            (session_id, role, content, sql, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def window(self, session_id: str, limit: int = 8) -> list[SessionMessage]:
        """最近 limit 条消息（按时间正序返回，供上下文装配）。"""
        rows = self.conn.execute(
            "SELECT id, session_id, role, content, sql, created_at FROM ("
            "  SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (session_id, limit),
        ).fetchall()
        return [SessionMessage(id=r[0], session_id=r[1], role=r[2], content=r[3],
                               sql=r[4], created_at=r[5]) for r in rows]

    def count(self, session_id: str) -> int:
        (n,) = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)).fetchone()
        return n

    # ---------- 会话摘要（M8 异步摘要） ----------

    def count_since(self, session_id: str, after_id: int) -> int:
        (n,) = self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ? AND id > ?",
            (session_id, after_id)).fetchone()
        return n

    def fetch_since(self, session_id: str, after_id: int,
                    exclude_last: int = 0) -> list[SessionMessage]:
        """水位之后的消息（时间正序）；exclude_last 排除最新 N 条（它们仍在窗口内）。"""
        rows = self.conn.execute(
            "SELECT id, session_id, role, content, sql, created_at FROM messages"
            " WHERE session_id = ? AND id > ? ORDER BY id ASC",
            (session_id, after_id),
        ).fetchall()
        if exclude_last:
            rows = rows[:-exclude_last] if len(rows) > exclude_last else []
        return [SessionMessage(id=r[0], session_id=r[1], role=r[2], content=r[3],
                               sql=r[4], created_at=r[5]) for r in rows]

    def save_summary(self, session_id: str, summary: str, until_id: int) -> None:
        self.conn.execute(
            "INSERT INTO sessions (session_id, summary, summarized_until, updated_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(session_id) DO UPDATE SET"
            " summary=excluded.summary, summarized_until=excluded.summarized_until,"
            " updated_at=excluded.updated_at",
            (session_id, summary, until_id, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def get_summary(self, session_id: str) -> tuple[str, int]:
        row = self.conn.execute(
            "SELECT summary, summarized_until FROM sessions WHERE session_id = ?",
            (session_id,)).fetchone()
        return (row[0] or "", row[1] or 0) if row else ("", 0)
