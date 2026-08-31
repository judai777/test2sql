"""schema 检索工具与 ask_user。"""
from __future__ import annotations

from t2s.tools import ToolContext


def test_search_by_risk_level(registry, ctx):
    out = registry.execute("search_schema", {"query": "客户风险等级分布"}, ctx)
    assert not out.is_error and "customers" in out


def test_search_by_branch_volume(registry, ctx):
    out = registry.execute("search_schema", {"query": "各营业部成交额排行"}, ctx)
    assert "branches" in out and "trades" in out


def test_search_no_hit_suggests_keywords(registry, ctx):
    out = registry.execute("search_schema", {"query": "咖啡拉花"}, ctx)
    assert "未检索到" in out


def test_get_schema_full_ddl(registry, ctx):
    out = registry.execute("get_schema", {"table": "customers"}, ctx)
    assert "CREATE TABLE customers" in out and "样例数据" in out
    assert "C1" in out  # 枚举值提示进入 DDL 注释
    assert "总行数" not in out  # CHG-014：行数会诱导模型跳过执行（以表行数当答案）


def test_get_schema_cached_per_turn(registry, ctx):
    first = registry.execute("get_schema", {"table": "customers"}, ctx)
    second = registry.execute("get_schema", {"table": "customers"}, ctx)
    assert "已提供过" in second  # 重复查看被提示（打转诱因治理）


def test_get_schema_suggests_similar(registry, ctx):
    out = registry.execute("get_schema", {"table": "customer"}, ctx)
    assert out.is_error and "customers" in out


def test_ask_user_with_asker(ctx):
    from t2s.tools import ToolContext
    ctx2 = ToolContext(db_path=ctx.db_path, ask_user=lambda q, options: "近30天")
    out = ctx2 and registry_ask(ctx2)
    assert "用户答复: 近30天" in out


def registry_ask(ctx) -> str:
    from t2s.tools import build_registry
    return build_registry().execute("ask_user", {"question": "统计区间？"}, ctx)


def test_ask_user_fallback_without_terminal(ctx):
    out = registry_ask(ctx)
    assert "合理假设" in out
