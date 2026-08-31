"""测试公共夹具：从真实 schema.sql 造迷你库（保证与 metadata 一致），几行样例数据。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from t2s.tools import ToolContext, ToolRegistry, build_registry

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "mini.db"
    conn = sqlite3.connect(p)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.executemany("INSERT INTO branches VALUES (?,?,?,?,?)", [
        (1, "北京望京证券营业部", "北京", "张三", "2020-01-01"),
        (2, "上海陆家嘴证券营业部", "上海", "李四", "2021-06-01"),
    ])
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (1, "王五", "M", "1980-05-01", "199000000001", "C3", "工程师", "北京", "2022-01-01", 1),
        (2, "赵六", "F", "1992-08-15", "199000000002", "C4", "医生", "上海", "2023-03-01", 2),
    ])
    conn.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", [
        (1, 1, 1, "普通", "正常", "2022-01-01", 100000.0, 250000.0),
        (2, 2, 2, "信用", "正常", "2023-03-01", 50000.0, 800000.0),
    ])
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", [
        (1, "600000", "测试银行", "股票", "银行", "SSE", "2020-01-01", 1),
        (2, "510300", "测试沪深300ETF", "ETF", "金融", "SSE", "2020-01-01", 1),
    ])
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (i, 100 + i, (i % 2) + 1, (i % 2) + 1, "B" if i % 2 else "S",
         10.0 + i, 100 * i, (10.0 + i) * 100 * i, 5.0, "2026-08-1" + str(i % 10), "09:30:00")
        for i in range(1, 9)
    ])
    conn.commit()
    conn.close()
    return p


@pytest.fixture()
def ctx(db_path: Path) -> ToolContext:
    return ToolContext(db_path=db_path, sql_timeout_s=5.0, sql_row_limit=100)


@pytest.fixture()
def registry() -> ToolRegistry:
    return build_registry()
