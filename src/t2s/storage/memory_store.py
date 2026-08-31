"""记忆存储：样例库（qa_pairs）与口径库（metrics_docs）。

向量以 JSON 存 SQLite（ADR-007）：量级 <1k，Python 余弦足够；
Embedding 缺失的行仍可被关键词降级检索命中。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from t2s.models.records import MetricDoc, QAPair


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _vecToJson(vec: list[float] | None) -> str | None:
    return json.dumps(vec) if vec is not None else None


def _jsonToVec(raw: str | None) -> list[float] | None:
    if not raw:
        return None
    try:
        vec = json.loads(raw)
        return vec if isinstance(vec, list) else None
    except json.JSONDecodeError:
        return None


class MemoryStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---------- 样例库（M8 确认制：candidate → confirmed） ----------

    def add_pair(self, question: str, sql: str, embedding: list[float] | None = None,
                 status: str = "candidate") -> int:
        """按 question 幂等 upsert：重复采纳同一问题 → 更新 SQL 与向量。

        status='candidate'（双通过自动沉淀，待用户确认）/'confirmed'（种子或已确认）。
        upsert 不回退已确认状态（candidate 覆盖 confirmed 会被下面的 COALESCE 挡住）。
        """
        now = _now()
        self.conn.execute(
            "INSERT INTO qa_pairs (question, sql, embedding, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(question) DO UPDATE SET"
            " sql=excluded.sql, embedding=excluded.embedding, updated_at=excluded.updated_at",
            (question, sql, _vecToJson(embedding), status, now, now),
        )
        # 确认制不回退：已 confirmed 的行不被 candidate 覆盖
        if status == "confirmed":
            self.conn.execute("UPDATE qa_pairs SET status='confirmed' WHERE question=? AND status='candidate'",
                              (question,))
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM qa_pairs WHERE question = ?", (question,)).fetchone()
        return int(row[0])

    def confirm_pair(self, pair_id: int) -> bool:
        """用户确认：候选 → 正式库（M8 确认制）。"""
        cur = self.conn.execute(
            "UPDATE qa_pairs SET status='confirmed' WHERE id=? AND status='candidate'", (pair_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def remove_pair(self, pair_id: int) -> bool:
        """用户点踩删除（PRD FR-8）。"""
        cur = self.conn.execute("DELETE FROM qa_pairs WHERE id = ?", (pair_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def all_pairs(self, status: str | None = "confirmed") -> list[QAPair]:
        """status='confirmed' 只取正式库；None 取全部（管理视图）。"""
        if status is None:
            rows = self.conn.execute(
                "SELECT id, question, sql, embedding, status, created_at, updated_at"
                " FROM qa_pairs ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, question, sql, embedding, status, created_at, updated_at"
                " FROM qa_pairs WHERE status = ? ORDER BY id", (status,)).fetchall()
        return [QAPair(id=r[0], question=r[1], sql=r[2], embedding=_jsonToVec(r[3]),
                       status=r[4], created_at=r[5], updated_at=r[6]) for r in rows]

    def candidate_pairs(self) -> list[QAPair]:
        return self.all_pairs(status="candidate")

    # ---------- 口径库 ----------

    def add_metric(self, title: str, content: str, embedding: list[float] | None = None) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO metrics_docs (title, content, embedding, created_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(title) DO UPDATE SET"
            " content=excluded.content, embedding=excluded.embedding",
            (title, content, _vecToJson(embedding), now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM metrics_docs WHERE title = ?", (title,)).fetchone()
        return int(row[0])

    def all_metrics(self) -> list[MetricDoc]:
        rows = self.conn.execute(
            "SELECT id, title, content, embedding, created_at FROM metrics_docs ORDER BY id"
        ).fetchall()
        return [MetricDoc(id=r[0], title=r[1], content=r[2], embedding=_jsonToVec(r[3]),
                          created_at=r[4]) for r in rows]

    # ---------- 表格/字段复用统计（ADR-008 D4） ----------

    def bump_usage(self, tables: list[str], fields: list[str]) -> None:
        """累计使用频次；表/字段分别计数。空入参静默跳过。"""
        if not tables and not fields:
            return
        now = _now()
        for kind, names in (("table", tables), ("field", fields)):
            for name in names:
                if not name:
                    continue
                self.conn.execute(
                    "INSERT INTO schema_usage (kind, name, hits, last_used) VALUES (?,?,1,?)"
                    " ON CONFLICT(kind, name) DO UPDATE SET"
                    " hits = hits + 1, last_used = excluded.last_used",
                    (kind, name.lower(), now),
                )
        self.conn.commit()

    def usage_map(self, kind: str = "table") -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT name, hits FROM schema_usage WHERE kind = ?", (kind,)).fetchall()
        return {r[0]: r[1] for r in rows}

    # ---------- 结果表格记忆（ADR-008 D2） ----------

    def save_result(self, title: str, question: str, sql: str,
                    columns: list[str], rows: list[list],
                    embedding: list[float] | None = None) -> int:
        """用户主动保存的查询结果表（≤50 行样本）。title 唯一，重复保存即更新。"""
        cur = self.conn.execute(
            "INSERT INTO saved_results (title, question, sql, columns, rows, embedding, created_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(title) DO UPDATE SET"
            " question=excluded.question, sql=excluded.sql, columns=excluded.columns,"
            " rows=excluded.rows, embedding=excluded.embedding",
            (title, question, sql, json.dumps(columns, ensure_ascii=False),
             json.dumps(rows, ensure_ascii=False, default=str), _vecToJson(embedding), _now()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM saved_results WHERE title = ?", (title,)).fetchone()
        return int(row[0])

    def all_results(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, title, question, sql, columns, rows, embedding, created_at"
            " FROM saved_results ORDER BY id DESC").fetchall()
        out = []
        for r in rows:
            try:
                cols = json.loads(r[4]); data = json.loads(r[5])
            except json.JSONDecodeError:
                cols, data = [], []
            out.append({"id": r[0], "title": r[1], "question": r[2], "sql": r[3],
                        "columns": cols, "rows": data,
                        "embedding": _jsonToVec(r[6]), "created_at": r[7]})
        return out

    def remove_result(self, result_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM saved_results WHERE id = ?", (result_id,))
        self.conn.commit()
        return cur.rowcount > 0
