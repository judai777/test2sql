"""M1 手动冒烟：对真实库（db/securities.db，219 万行）逐个调用五件套。

用法: PYTHONPATH=src python scripts/smoke_tools.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from t2s.config import AppConfig
from t2s.tools import ToolContext, build_registry

BANNER = "\n" + "=" * 72


def show(title: str, result: str, limit: int = 700) -> None:
    print(f"{BANNER}\n▶ {title}\n{'-' * 72}\n{result[:limit]}{'  …[截断]' if len(result) > limit else ''}\n[is_error={getattr(result, 'is_error', None)}]")


def main() -> None:
    cfg = AppConfig.load()
    ctx = ToolContext(
        db_path=cfg.tools.db_path,
        sql_timeout_s=cfg.tools.sql_timeout_s,
        sql_row_limit=cfg.tools.sql_row_limit,
    )
    registry = build_registry()

    show("1. search_schema：客户风险等级",
         registry.execute("search_schema", {"query": "客户风险等级分布"}, ctx))
    show("2. search_schema：各营业部成交额",
         registry.execute("search_schema", {"query": "近一月各营业部成交额排行"}, ctx))
    show("3. get_schema：trades（真实 100 万行表）",
         registry.execute("get_schema", {"table": "trades"}, ctx), limit=900)
    show("4. validate_sql：合法查询",
         registry.execute("validate_sql", {"sql": (
             "SELECT b.branch_name, ROUND(SUM(t.amount)/1e8, 2) AS yi "
             "FROM trades t JOIN accounts a ON a.account_id = t.account_id "
             "JOIN branches b ON b.branch_id = a.branch_id "
             "WHERE t.trade_date >= '2026-08-01' "
             "GROUP BY b.branch_name ORDER BY yi DESC LIMIT 5")}, ctx))
    show("5. validate_sql：幻觉表",
         registry.execute("validate_sql", {"sql": "SELECT * FROM user_info"}, ctx))
    show("6. validate_sql：写操作",
         registry.execute("validate_sql", {"sql": "DELETE FROM trades WHERE trade_id = 1"}, ctx))
    show("7. execute_sql：三表 JOIN 真实执行（百万行）",
         registry.execute("execute_sql", {"sql": (
             "SELECT b.branch_name, ROUND(SUM(t.amount)/1e8, 2) AS yi "
             "FROM trades t JOIN accounts a ON a.account_id = t.account_id "
             "JOIN branches b ON b.branch_id = a.branch_id "
             "WHERE t.trade_date >= '2026-08-01' "
             "GROUP BY b.branch_name ORDER BY yi DESC")}, ctx))
    show("8. execute_sql：无 LIMIT 自动包裹",
         registry.execute("execute_sql", {"sql": "SELECT customer_id, name FROM customers"}, ctx), limit=400)
    show("9. ask_user：无交互终端降级",
         registry.execute("ask_user", {"question": "月活跃的定义是月内有交易还是月内有登录？"}, ctx))


if __name__ == "__main__":
    main()
