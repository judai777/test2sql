"""SQL 护栏：只读断言、解析限制、LIMIT 强制、超时中断。"""
from __future__ import annotations

import json

from t2s.tools import ToolContext


def run(registry, ctx, name, args):
    out = registry.execute(name, args, ctx)
    assert isinstance(out, str)
    return out


# ---------- validate_sql ----------

def test_validate_accepts_select(registry, ctx):
    out = json.loads(run(registry, ctx, "validate_sql", {"sql": "SELECT COUNT(*) FROM trades"}))
    assert out["valid"] is True and "trades" in out["tables_used"]


def test_validate_rejects_write(registry, ctx):
    out = json.loads(run(registry, ctx, "validate_sql", {"sql": "DELETE FROM trades"}))
    assert out["valid"] is False and "硬边界" in out["errors"][0]


def test_validate_rejects_multi_statement(registry, ctx):
    out = json.loads(run(registry, ctx, "validate_sql", {"sql": "SELECT 1; SELECT 2"}))
    assert out["valid"] is False and "单条" in out["errors"][0]


def test_validate_unknown_table(registry, ctx):
    out = json.loads(run(registry, ctx, "validate_sql", {"sql": "SELECT * FROM users"}))
    assert out["valid"] is False and "users" in out["errors"][0]


def test_validate_typo_column_single_table(registry, ctx):
    out = json.loads(run(registry, ctx, "validate_sql", {"sql": "SELECT trade_dat FROM trades"}))
    assert out["valid"] is False and "不存在" in out["errors"][0]


def test_validate_cte_ok(registry, ctx):
    out = json.loads(run(registry, ctx, "validate_sql", {
        "sql": "WITH t AS (SELECT account_id, amount FROM trades) SELECT account_id FROM t"}))
    assert out["valid"] is True


def test_validate_qualified_column_across_join(registry, ctx):
    sql = ("SELECT b.branch_name FROM trades t "
           "JOIN accounts a ON a.account_id = t.account_id "
           "JOIN branches b ON b.branch_id = a.branch_id")
    out = json.loads(run(registry, ctx, "validate_sql", {"sql": sql}))
    assert out["valid"] is True


def test_validate_qualified_typo(registry, ctx):
    out = json.loads(run(registry, ctx, "validate_sql", {
        "sql": "SELECT b.branch_nam FROM trades t JOIN branches b ON b.branch_id = t.account_id"}))
    assert out["valid"] is False and "branch_nam" in out["errors"][0]


# ---------- execute_sql ----------

def test_execute_select(registry, ctx):
    payload = json.loads(run(registry, ctx, "execute_sql", {"sql": "SELECT side, COUNT(*) AS n FROM trades GROUP BY side"}))
    assert payload["row_count"] == 2 and payload["columns"] == ["side", "n"] and payload["elapsed_ms"] >= 0


def test_execute_write_blocked(registry, ctx):
    out = run(registry, ctx, "execute_sql", {"sql": "UPDATE trades SET price = 1"})
    assert "硬边界" in out


def test_execute_auto_limit(registry, ctx):
    small = ToolContext(db_path=ctx.db_path, sql_timeout_s=5.0, sql_row_limit=3)
    payload = json.loads(run(registry, small, "execute_sql", {"sql": "SELECT * FROM trades ORDER BY trade_id"}))
    assert payload["limit_applied"] is True and payload["row_count"] == 3 and "截断" in payload["note"]


def test_execute_existing_limit_kept(registry, ctx):
    payload = json.loads(run(registry, ctx, "execute_sql", {"sql": "SELECT * FROM trades LIMIT 2"}))
    assert payload["limit_applied"] is False and payload["row_count"] == 2


def test_execute_timeout_interrupt(registry, ctx):
    zero = ToolContext(db_path=ctx.db_path, sql_timeout_s=0.0, sql_row_limit=100)
    # 迷你库查询太轻触发不了 progress handler；COUNT 会走 B-tree 计数捷径，
    # 用四表笛卡尔积求 SUM 强制逐行 VM 求值
    out = run(registry, zero, "execute_sql",
              {"sql": "SELECT SUM(a.trade_id + b.trade_id + c.trade_id + d.trade_id) "
                      "FROM trades a, trades b, trades c, trades d"})
    assert out.is_error and "超时" in out


def test_limit_injection_preserves_order(registry, ctx):
    """CHG-014：LIMIT 注入不得破坏 ORDER BY（子查询包裹缺陷回归锚点）。"""
    payload = json.loads(run(registry, ctx, "execute_sql",
                             {"sql": "SELECT trade_id, amount FROM trades ORDER BY amount DESC"}))
    assert payload["rows"][0][0] == 8  # 最大单笔的 trade_id 必须排第一


def test_execute_ro_connection_blocks_writes(registry, ctx):
    # 绕过关键字黑名单的场景难以构造；直接验证 ro 通道：pragma 写模式被拒
    out = run(registry, ctx, "execute_sql", {"sql": "PRAGMA journal_mode = DELETE"})
    assert out.is_error
