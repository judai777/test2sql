"""SQL 相关工具：search_schema / get_schema / validate_sql / execute_sql。

安全护栏（ADR-001 D4/D6，对应 DB-GPT 调研报告 §3.5/§3.6 的改进实现）：
1. 关键字黑名单拒绝写操作与 DDL（硬边界，提示词明说不可重试）；
2. sqlglot 解析限定单条只读查询（SELECT / WITH...SELECT）；
3. 表与列存在性白盒校验（DB-GPT 未做完的一环，本项目增量）；
4. SQLite 只读连接（mode=ro，即使护栏漏网也无法落盘写入）；
5. 无 LIMIT 自动包裹 + 行数上限 + progress handler 墙钟中断。
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import time
from urllib.parse import quote

import sqlglot
from sqlglot import exp

from t2s.tools.base import RETRY_HINT, Tool, ToolContext, ToolResult
from t2s.tools.metadata import BY_NAME, TableMeta, render_ddl
from t2s.utils.text import bigram_score, query_terms

_WRITE_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|attach|detach|pragma|replace|grant|revoke|vacuum|reindex)\b",
    re.IGNORECASE,
)
_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def _connect_ro(ctx: ToolContext) -> sqlite3.Connection:
    uri = f"file:{quote(ctx.db_path.resolve().as_posix())}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _lint(sql: str) -> tuple[exp.Expression | None, str | None]:
    """解析为单条只读语句。返回 (stmt, error)。"""
    if _WRITE_RE.search(sql):
        return None, "包含写操作/DDL 关键字。这是安全硬边界（只读库），请改用 SELECT 查询，或直接向用户说明无法执行。"
    try:
        stmts = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.ParseError as e:
        return None, f"SQL 语法错误: {e}"
    if len(stmts) != 1:
        return None, "只允许单条查询语句（检测到多条）。"
    stmt = stmts[0]
    if not isinstance(stmt, _READ_ROOTS):
        return None, "仅支持只读查询（SELECT / UNION / WITH...SELECT），写操作与 DDL 一律拒绝。"
    return stmt, None


def _check_tables_and_columns(stmt: exp.Expression) -> tuple[list[str], str | None]:
    cte_names = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    tables: list[exp.Table] = []
    for t in stmt.find_all(exp.Table):
        if t.name.lower() in cte_names:
            continue
        tables.append(t)

    used: list[str] = []
    alias_map: dict[str, str] = {}
    for t in tables:
        name = t.name
        if name.lower() not in BY_NAME:
            return [], f"表 '{name}' 不在业务库中。可用表: {', '.join(sorted(BY_NAME))}。请先用 search_schema 检索。"
        used.append(name)
        if t.alias:
            alias_map[t.alias.lower()] = name

    distinct = set(used)
    for col in stmt.find_all(exp.Column):
        cname = col.name
        if not cname or cname == "*":
            continue
        ref = (col.table or "").lower()
        resolved = alias_map.get(ref, ref) if ref else ""
        if resolved:
            meta = BY_NAME.get(resolved)
            if meta and cname.lower() not in meta.column_names:
                return used, f"列 '{resolved}.{cname}' 不存在。请先用 get_schema 查看表结构。"
        elif not ref and len(distinct) == 1 and not cte_names:
            meta = BY_NAME[next(iter(distinct))]
            if cname.lower() not in meta.column_names:
                return used, f"列 '{cname}' 不存在于表 {meta.name} 中。请先用 get_schema 查看表结构。"
    return used, None


def _search_tables(query: str, top_k: int) -> list[tuple[TableMeta, int]]:
    def score(t: TableMeta) -> int:
        # 按出现次数计分（purpose 计 3 倍权重），比"是否命中"更能区分主表与沾边表
        return sum(3 * t.purpose.count(term) + t.search_corpus.count(term)
                   for term in query_terms(query))

    scored = sorted(BY_NAME.values(), key=lambda t: (-score(t), t.name))
    return [(t, score(t)) for t in scored[:top_k]]


def _permission_check(used: list[str], ctx: ToolContext) -> str | None:
    """权限硬约束（ADR-008 D6）：实际访问表 ⊆ 用户白名单；None = 全表只读。"""
    if ctx.allowed_tables is None:
        return None
    disallowed = sorted(set(t.lower() for t in used) - set(ctx.allowed_tables))
    if disallowed:
        return (f"权限边界（不可重试）：当前用户无权访问表 {', '.join(disallowed)}。"
                "请改用授权范围内的表，或向管理员申请权限。")
    return None


class SearchSchema(Tool):
    name = "search_schema"
    description = (
        "按业务语义检索相关数据表，返回表名、业务用途与列摘要。"
        "不知道该查哪张表、或问题涉及不熟悉的业务概念时，先调用本工具。"
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "description": "业务语义描述，如：客户风险等级分布 / 近一月各营业部成交额"},
            "top_k": {"type": "integer", "description": "返回相关表数量，默认 3"},
        },
        "required": ["query"],
    }

    def execute(self, ctx: ToolContext, query: str, top_k: int = 3) -> ToolResult:
        # M7：混合检索可用（管理层注入 retriever）时走"关键词主导+语义辅助+复用加权"；
        # 缺省保持关键词兜底（ADR-008 D3）。
        if ctx.retriever is not None:
            hits = ctx.retriever.search_tables(query, top_k=max(1, min(top_k, 5)))
            if not hits:
                return ToolResult.ok(
                    "未检索到相关表。请换业务关键词（如：持仓 / 成交 / 营业部 / 行情 / 净值 / 预警 / 两融）再试。"
                )
            lines = [f"[相关度 {score:.2f}] {name}: {BY_NAME[name].purpose}\n"
                     f"  列: {', '.join(c.name for c in BY_NAME[name].columns)}"
                     for name, score in hits]
            return ToolResult.ok("\n".join(lines) + "\n\n下一步：用 get_schema 取目标表的完整 DDL。")

        hits = _search_tables(query, max(1, min(top_k, 5)))
        if not hits or hits[0][1] == 0:
            return ToolResult.ok(
                "未检索到相关表。请换业务关键词（如：持仓 / 成交 / 营业部 / 行情 / 净值 / 预警 / 两融）再试。"
            )
        lines = []
        for t, score in hits:
            cols = ", ".join(c.name for c in t.columns)
            lines.append(f"[相关度 {score}] {t.name}: {t.purpose}\n  列: {cols}")
        return ToolResult.ok("\n".join(lines) + "\n\n下一步：用 get_schema 取目标表的完整 DDL。")


class GetSchema(Tool):
    name = "get_schema"
    description = "取某张表的完整 DDL（含列注释与枚举取值）、总行数与 2 行样例数据。写 SQL 前必须确认表结构。"
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"table": {"type": "string", "description": "表名，如 trades"}},
        "required": ["table"],
    }

    def execute(self, ctx: ToolContext, table: str) -> ToolResult:
        meta = BY_NAME.get(table.lower())
        if meta is None:
            match = difflib.get_close_matches(table.lower(), list(BY_NAME), n=1, cutoff=0.6)
            hint = f"你是不是想查 '{match[0]}'？" if match else ""
            return ToolResult.error(f"表 '{table}' 不存在。可用表: {', '.join(sorted(BY_NAME))}。{hint}")
        # per-turn 缓存（CHG-014）：同表重复 get_schema 是打转诱因——返回缓存并附提醒
        cache: dict = ctx.extra.setdefault("schema_cache", {})
        key = meta.name.lower()
        if key in cache:
            return ToolResult.ok(
                cache[key] + "\n[提示] 该表结构已提供过，请直接编写 SQL（重复查看会被防护拦截）。")
        conn = _connect_ro(ctx)
        try:
            sample = conn.execute(f"SELECT * FROM {meta.name} LIMIT 2").fetchall()
            cols = [d[0] for d in conn.execute(f"SELECT * FROM {meta.name} LIMIT 0").description]
        finally:
            conn.close()
        sample_json = json.dumps([dict(zip(cols, r)) for r in sample], ensure_ascii=False, default=str)
        # 注意：不返回表总行数——实测会诱导模型把"表行数"当作查询答案、跳过执行（CHG-014）
        text = f"{render_ddl(meta)}\n\n-- 样例数据(2行): {sample_json}"
        cache[key] = text
        return ToolResult.ok(text)


class ValidateSql(Tool):
    name = "validate_sql"
    description = (
        "执行前白盒校验 SQL：语法解析（sqlite 方言）、仅允许单条只读查询、表与列存在性检查。"
        "生成 SQL 后必须先调用本工具，valid=true 再调用 execute_sql。"
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"sql": {"type": "string", "description": "待校验的完整 SQL"}},
        "required": ["sql"],
    }

    def execute(self, ctx: ToolContext, sql: str) -> ToolResult:
        stmt, err = _lint(sql)
        if err is not None:
            return ToolResult.ok(json.dumps({"valid": False, "errors": [err]}, ensure_ascii=False))
        used, err = _check_tables_and_columns(stmt)
        if err is not None:
            return ToolResult.ok(json.dumps({"valid": False, "errors": [err]}, ensure_ascii=False))
        perm = _permission_check(used, ctx)
        if perm is not None:
            return ToolResult.ok(json.dumps({"valid": False, "errors": [perm]}, ensure_ascii=False))
        return ToolResult.ok(json.dumps(
            {"valid": True, "tables_used": sorted(set(used)), "note": "校验通过，可调用 execute_sql 执行"},
            ensure_ascii=False,
        ))


class ExecuteSql(Tool):
    name = "execute_sql"
    description = (
        "在只读连接上执行已通过 validate_sql 的查询。自动补 LIMIT、强制行数上限、超时中断。"
        "任何写操作都会被拒绝。"
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"sql": {"type": "string", "description": "已通过 validate_sql 的完整 SQL"}},
        "required": ["sql"],
    }

    def execute(self, ctx: ToolContext, sql: str) -> ToolResult:
        stmt, err = _lint(sql)
        if err is not None:
            return ToolResult.error(err)
        used, err = _check_tables_and_columns(stmt)
        if err is not None:
            return ToolResult.error(err + RETRY_HINT)
        perm = _permission_check(used, ctx)
        if perm is not None:
            return ToolResult.error(perm)

        # LIMIT 注入（CHG-014 修复）：用 sqlglot 在原语句上设置 LIMIT——保留 ORDER BY
        # 原位语义。旧实现 `SELECT * FROM (原语句) LIMIT n` 会把子查询内 ORDER BY 的
        # 顺序保证破坏掉（SQL 标准中子查询内排序无意义），导致有序结果乱序（v2 回归实测）。
        limit_applied = False
        existing = stmt.find(exp.Limit)
        if existing is None:
            stmt = stmt.limit(ctx.sql_row_limit)
            limit_applied = True
        else:
            literal = existing.expression
            try:
                value = int(literal.this)  # type: ignore[union-attr]
            except (AttributeError, ValueError, TypeError):
                value = None
            if value is None or value > ctx.sql_row_limit:
                stmt = stmt.limit(ctx.sql_row_limit)
                limit_applied = True
        sql_exec = stmt.sql(dialect="sqlite")

        conn = _connect_ro(ctx)
        # perf_counter 单调高精度：Windows time.time() 粒度 ~15ms，短超时/快查询会漏判
        deadline = time.perf_counter() + ctx.sql_timeout_s
        conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 1000)
        t0 = time.perf_counter()
        try:
            cur = conn.execute(sql_exec)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        except sqlite3.OperationalError as e:
            if "interrupt" in str(e).lower():
                return ToolResult.error(
                    f"执行超时（>{ctx.sql_timeout_s:.0f}s 已中断）。请缩小时间范围、先 COUNT 估算、或加过滤条件后重试。"
                )
            return ToolResult.error(f"SQL 执行错误: {e}{RETRY_HINT}")
        except sqlite3.Error as e:
            return ToolResult.error(f"SQL 执行错误: {e}{RETRY_HINT}")
        finally:
            conn.close()

        payload = {
            "columns": cols,
            "rows": [[_cell(v) for v in r] for r in rows],
            "row_count": len(rows),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000),
            "limit_applied": limit_applied,
        }
        if limit_applied and len(rows) >= ctx.sql_row_limit:
            payload["note"] = f"结果已截断到 {ctx.sql_row_limit} 行；如需全貌请用聚合或分页。"
        return ToolResult.ok(json.dumps(payload, ensure_ascii=False, default=str))


def _cell(v) -> object:
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, bytes):
        return f"<binary {len(v)} bytes>"
    return v
