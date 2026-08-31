"""MCP 服务端（ADR-008 D1）：把数据库工具（Tool 单一事实来源）经 MCP 协议暴露。

- coder 子 agent 经 MCP 客户端调用（tools/mcp_bridge.py 的内存传输），外部 MCP client
  （如 Claude Code）亦可直连（stdio 入口见 __main__）。
- 错误语义跨协议保留：is_error 经 JSON 信封显式传递，护栏文案不变（AGENTS.md §4）。
- 服务端 ctx 绑定只读数据库连接参数；ask_user 由委派方在运行时透传（human-in-the-loop）。
"""
from __future__ import annotations

import json

from t2s.tools import ToolContext, build_registry


def build_db_mcp_server(server_ctx: ToolContext):
    from fastmcp import FastMCP

    mcp = FastMCP("test2sql-db")
    registry = build_registry()  # 与主循环同一套 Tool 实现（单一事实来源，护栏随实现走）

    def _run(name: str, args: dict) -> str:
        result = registry.execute(name, args, server_ctx)
        return json.dumps({"ok": not result.is_error, "output": str(result)},
                          ensure_ascii=False)

    @mcp.tool
    def get_schema(table: str) -> str:
        """取某张表的完整 DDL（列注释/枚举/外键）、总行数与 2 行样例。"""
        return _run("get_schema", {"table": table})

    @mcp.tool
    def validate_sql(sql: str) -> str:
        """校验 SQL：语法（sqlite 方言）、只读断言、表列存在性、权限白名单。返回 JSON {valid, errors, tables_used}。"""
        return _run("validate_sql", {"sql": sql})

    @mcp.tool
    def execute_sql(sql: str) -> str:
        """在只读连接上执行 SQL（自动 LIMIT、行数上限、超时中断；写操作硬边界拒绝）。返回 JSON {columns, rows, row_count, elapsed_ms}。"""
        return _run("execute_sql", {"sql": sql})

    @mcp.tool
    def ask_user(question: str, options: list[str] | None = None) -> str:
        """信息不足以生成 SQL 时，向用户发起一次澄清（human-in-the-loop）。"""
        return _run("ask_user", {"question": question, "options": options})

    return mcp
