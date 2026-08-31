"""四层死循环防护（ADR-001 D6 / PRD FR-4）。

- ① 步数上限：由引擎 for 循环控制，耗尽走无工具强制总结
- ② 打转检测：同 (tool, args哈希) 连续重复 → warn；累计超限 → stop；A→B→A→B 振荡 → warn
- ③ 错误回喂限次：同一 SQL 第 N 次报错 → warn，超过 max_repair_same_sql → stop（诚实失败）
- ④ 墙钟：单 turn deadline，超时 → stop

verdict 语义：allow=正常执行；warn=跳过本次执行、把警告当观察回填；stop=终止本 turn。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from t2s.models.task import Budget


@dataclass
class GuardDecision:
    verdict: str          # "allow" | "warn" | "stop"
    reason: str = ""


@dataclass
class LoopGuard:
    budget: Budget
    deadline: float                  # perf_counter 时钟域（Windows time.time() 精度 ~15ms，不可用）
    _history: list[tuple[str, str]] = field(default_factory=list, repr=False)
    _streak: int = 0
    _repeat_total: int = 0
    _sql_errors: dict[str, int] = field(default_factory=dict)
    steps_used: int = 0

    @staticmethod
    def _sig(tool: str, args: dict) -> tuple[str, str]:
        try:
            return (tool, json.dumps(args, sort_keys=True, ensure_ascii=False))
        except (TypeError, ValueError):
            return (tool, repr(args))

    def check_wall_clock(self) -> GuardDecision:
        if time.perf_counter() > self.deadline:
            return GuardDecision("stop", "wall_clock")
        return GuardDecision("allow")

    def record_action(self, tool: str, args: dict) -> GuardDecision:
        """动作执行前登记：识别连续重复与振荡。"""
        sig = self._sig(tool, args)
        self.steps_used += 1
        self._history.append(sig)
        oscillation = False
        if len(self._history) >= 2 and self._history[-1] == self._history[-2]:
            self._streak += 1
            self._repeat_total = max(self._repeat_total, self._streak)
        elif len(self._history) >= 4 and self._history[-2:] == self._history[-4:-2]:
            oscillation = True
            self._repeat_total += 1
            self._streak = 0
        else:
            self._streak = 1
        if self._repeat_total >= self.budget.repeat_stop_at:
            return GuardDecision("stop", "repeat_stop")
        if self._streak >= self.budget.repeat_warn_at:
            return GuardDecision(
                "warn",
                "你正在连续重复同一动作（相同工具+相同参数）。请换一种方法、换一个查询，或明确承认无法完成。",
            )
        if oscillation:
            return GuardDecision(
                "warn",
                "你陷入了 A→B→A→B 循环。请跳出当前路径：换思路或承认失败。",
            )
        return GuardDecision("allow")

    def record_sql_error(self, sql: str) -> GuardDecision:
        """execute_sql 报错后登记：同一 SQL 的回喂限次。"""
        count = self._sql_errors.get(sql, 0) + 1
        self._sql_errors[sql] = count
        if count > self.budget.max_repair_same_sql:
            return GuardDecision("stop", "repair_limit")
        if count == self.budget.max_repair_same_sql:
            return GuardDecision(
                "warn",
                "同一条 SQL 已失败两次。请不要原样重试：改写、拆解，或先用 get_schema 核对表结构。",
            )
        return GuardDecision("allow")
