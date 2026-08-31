"""四层防护单元测试：②打转检测（重复/振荡）③错误回喂限次 ④墙钟。"""
from __future__ import annotations

import time

from t2s.executor.guard import LoopGuard
from t2s.models.task import Budget


def guard(**budget_kw) -> LoopGuard:
    return LoopGuard(Budget(**budget_kw), deadline=time.time() + 60)


def test_first_action_allowed():
    g = guard()
    assert g.record_action("get_schema", {"table": "trades"}).verdict == "allow"


def test_consecutive_repeat_warns_then_stops():
    g = guard(repeat_stop_at=3)
    assert g.record_action("get_schema", {"table": "trades"}).verdict == "allow"
    d2 = g.record_action("get_schema", {"table": "trades"})
    assert d2.verdict == "warn" and "重复" in d2.reason
    d3 = g.record_action("get_schema", {"table": "trades"})
    assert d3.verdict == "stop" and d3.reason == "repeat_stop"


def test_args_difference_matters():
    g = guard()
    g.record_action("get_schema", {"table": "trades"})
    # 参数不同不算重复（序列化后哈希不同）
    assert g.record_action("get_schema", {"table": "customers"}).verdict == "allow"


def test_alternating_actions_no_warn():
    g = guard()
    for i in range(4):
        tool = "get_schema" if i % 2 == 0 else "validate_sql"
        assert g.record_action(tool, {"i": i}).verdict == "allow"


def test_oscillation_abab_detected():
    g = guard()
    first_three = [g.record_action(t, a).verdict for t, a in
                   [("a", {"x": 1}), ("b", {"x": 2}), ("a", {"x": 1})]]
    assert first_three == ["allow", "allow", "allow"]
    d4 = g.record_action("b", {"x": 2})
    assert d4.verdict == "warn" and "循环" in d4.reason
    assert g.record_action("c", {"x": 3}).verdict == "allow"  # 跳出后恢复


def test_sql_error_feedback_limit():
    g = guard(max_repair_same_sql=2)
    sql = "SELECT nocol FROM trades"
    assert g.record_sql_error(sql).verdict == "allow"        # 第 1 次：回喂
    d2 = g.record_sql_error(sql)
    assert d2.verdict == "warn" and "两次" in d2.reason       # 第 2 次：警告
    d3 = g.record_sql_error(sql)
    assert d3.verdict == "stop" and d3.reason == "repair_limit"  # 第 3 次：诚实失败


def test_sql_error_counter_is_per_sql():
    g = guard(max_repair_same_sql=2)
    g.record_sql_error("SELECT 1")
    assert g.record_sql_error("SELECT 2").verdict == "allow"


def test_wall_clock_expired():
    g = LoopGuard(Budget(), deadline=time.perf_counter() - 1)
    assert g.check_wall_clock().verdict == "stop"
    assert g.check_wall_clock().reason == "wall_clock"
