"""存储层：记忆/会话/审计库（db/memory.db）的唯一出入口（ADR-005）。

业务库 db/securities.db 的只读访问在 tools/sql_tools.py；本文件管的是系统自身的库。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id       TEXT PRIMARY KEY,
  summary          TEXT,                -- 滚动摘要（M8 异步摘要）
  summarized_until INTEGER NOT NULL DEFAULT 0,   -- 已摘要到的 message id 水位
  updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role       TEXT NOT NULL,           -- user | assistant
  content    TEXT NOT NULL,
  sql        TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS audit_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT NOT NULL,
  session_id   TEXT,
  user_id      TEXT,
  question     TEXT NOT NULL,
  intent       TEXT,
  stop_reason  TEXT,
  sql          TEXT,
  row_count    INTEGER,
  steps        INTEGER,
  total_tokens INTEGER,
  elapsed_ms   INTEGER,
  content      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS qa_pairs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  question   TEXT NOT NULL UNIQUE,
  sql        TEXT NOT NULL,
  embedding  TEXT,                 -- JSON 数组或 NULL（Embedding 不可用时）
  status     TEXT NOT NULL DEFAULT 'candidate',  -- candidate | confirmed（M8 确认制）
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_results (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL UNIQUE,
  question   TEXT NOT NULL,
  sql        TEXT NOT NULL,
  columns    TEXT NOT NULL,        -- JSON 数组
  rows       TEXT NOT NULL,        -- JSON 数组（≤50 行样本）
  embedding  TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_saved_results_title ON saved_results(title);

CREATE TABLE IF NOT EXISTS metrics_docs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  title      TEXT NOT NULL UNIQUE,
  content    TEXT NOT NULL,
  embedding  TEXT,
  created_at TEXT NOT NULL
);

-- 表格与字段复用统计（ADR-008 D4）：record_success 时 sqlglot 反解 SQL 累计
CREATE TABLE IF NOT EXISTS schema_usage (
  kind      TEXT NOT NULL,              -- table | field
  name      TEXT NOT NULL,
  hits      INTEGER NOT NULL DEFAULT 0,
  last_used TEXT NOT NULL,
  PRIMARY KEY (kind, name)
);
"""


def open_db(db_path: Path) -> sqlite3.Connection:
    """打开（并按需初始化）记忆库。幂等，含轻量迁移。

    check_same_thread=False：FastAPI 同步端点跑在线程池，连接须跨线程；
    写入由端点的串行调用语义保证（MVP 单用户），并发写保护在 M3+ 会话锁中处理。
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """存量库轻量迁移（幂等）：列缺失时补齐。"""
    qa_cols = {r[1] for r in conn.execute("PRAGMA table_info(qa_pairs)")}
    if qa_cols and "status" not in qa_cols:
        conn.execute("ALTER TABLE qa_pairs ADD COLUMN status TEXT NOT NULL DEFAULT 'candidate'")
    conn.commit()
