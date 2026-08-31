"""coder 委派（ADR-008 D1）：data-analysis 把"SQL 编写与执行"委派给 coder 专职 agent。

递归防护：子 agent 步数 = min(coder skill 上限, 父 turn 剩余步数 - 1)，
墙钟不超过父 turn 剩余时间——agent 套 agent 不可能突破父预算。
human-in-the-loop：coder 的 ask_user 经服务端 ctx 透传给父 turn 的用户通道。
"""
from __future__ import annotations

import time

from t2s.executor.skill_registry import Skill
from t2s.models.task import Budget, TaskRequest
from t2s.tools.base import Tool, ToolResult


class CoderDelegation:
    def __init__(self, coder_engine, server_ctx, skill: Skill) -> None:
        self.coder_engine = coder_engine
        self.server_ctx = server_ctx   # MCP 服务端绑定 ctx（ask_user 运行时透传）
        self.skill = skill

    def run(self, task: str, schema_context: str, parent_ctx) -> str:
        budget: Budget = parent_ctx.extra.get("budget") or Budget()
        guard = parent_ctx.extra.get("guard")
        used = guard.steps_used if guard else 0
        remaining = budget.max_steps - used
        if remaining <= 2:
            return ("预算不足以委派 coder（父 turn 剩余步数不足）。"
                    "请基于已有信息直接作答或承认失败。")
        child_steps = min(self.skill.max_steps, remaining - 1)

        child_timeout = 90.0
        deadline = parent_ctx.extra.get("turn_deadline")
        if deadline:
            child_timeout = min(child_timeout, max(deadline - time.perf_counter(), 15.0))
        child_budget = Budget(max_steps=child_steps,
                              turn_timeout_s=round(child_timeout, 1))

        # human-in-the-loop 透传：coder 的 ask_user 走父 turn 的用户交互通道
        if parent_ctx.ask_user:
            self.server_ctx.ask_user = parent_ctx.ask_user

        question = f"{task}\n\n[上游提供的 schema 信息]\n{schema_context or '（无，可用 get_schema 探查）'}"
        answer = self.coder_engine.run(
            TaskRequest(question=question, budget=child_budget), self.server_ctx)

        head = ("[coder 执行结果]\n" if answer.stop_reason == "final"
                else f"[coder 未能完成（{answer.stop_reason}）]\n")
        tail = f"\n[SQL] {answer.sql}" if answer.sql else ""
        return f"{head}{answer.content}{tail}"


class DelegateSqlTask(Tool):
    name = "delegate_sql_task"
    description = (
        "把'SQL 编写与执行'委派给 coder 专职 agent（其经 MCP 调用数据库工具）。"
        "适用：任务目标已明确、或你反复修复 SQL 失败时。传递任务描述与已掌握的 schema 信息；"
        "业务结论仍由你组织。每 turn 至多委派一次为宜。"
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task": {"type": "string",
                     "description": "明确的取数任务：指标、口径、时间范围、输出要求"},
            "schema_context": {"type": "string",
                               "description": "你已掌握的相关表结构/字段信息，可为空"},
        },
        "required": ["task"],
    }

    def __init__(self, delegation: CoderDelegation) -> None:
        self.delegation = delegation

    def execute(self, ctx, task: str, schema_context: str = "") -> ToolResult:
        return ToolResult.ok(self.delegation.run(
            task=task, schema_context=schema_context, parent_ctx=ctx))
