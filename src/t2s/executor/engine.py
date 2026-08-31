"""计划无关 ReAct 引擎（执行层核心，唯一 LLM 所在——ADR-001 D2/D3）。

三个不变量（nanobot 调研报告 §3/§6，测试钉住）：
1. 先声明后回填：assistant(tool_calls) 必须先于对应 role=tool 结果入列
2. 错误即观察：工具失败包成观察回填，引擎不向调用方抛业务异常
3. 拒绝不执行：带 tool_calls 但 finish_reason 不允许执行的响应，绝不触发工具

引擎"计划无关"：它只知道"跑步骤/调工具/回填观察"，不知道任务长什么样。
二期快路径 = 路由层以强制计划模式注入，本文件零改动。
"""
from __future__ import annotations

import json
import time
from typing import Callable

from t2s.executor.guard import GuardDecision, LoopGuard
from t2s.executor.prompts import (FALLBACK_SUMMARY, FEWSHOT_TEMPLATE, SUMMARY_REQUEST,
                                  _table_directory, build_system_prompt)
from t2s.llm import ChatMessage, LLMError
from t2s.models.task import Answer, TaskRequest, TraceStep
from t2s.tools import ToolContext, ToolRegistry

EventFn = Callable[[str, dict], None]


class ReActEngine:
    def __init__(self, llm, registry: ToolRegistry, *, on_event: EventFn | None = None,
                 system_prompt: str | None = None) -> None:
        self.llm = llm
        self.registry = registry
        self.on_event = on_event
        # skill 覆盖（ADR-008 D1）：注入时以 skill 系统提示词为准（agent 身份），
        # few-shot 记忆块仍在运行时追加；None = 默认 data-analysis 提示词
        self.system_prompt = system_prompt

    def _emit(self, event: str, **data) -> None:
        if self.on_event:
            self.on_event(event, data)

    # ---------- 主循环 ----------

    def run(self, task: TaskRequest, ctx: ToolContext,
            history: list[ChatMessage] | None = None, few_shot: str = "") -> Answer:
        start = time.perf_counter()  # 单调高精度：Windows time.time() 粒度 ~15ms，会让短超时失效
        guard = LoopGuard(task.budget, deadline=start + task.budget.turn_timeout_s)
        ctx.extra["budget"] = task.budget      # 委派工具读取（递归预算防护，ADR-008 D1）
        ctx.extra["guard"] = guard
        ctx.extra["turn_deadline"] = guard.deadline
        usage = {"prompt": 0, "completion": 0, "total": 0}
        trace: list[TraceStep] = []
        last_sql: str | None = None
        row_count: int | None = None
        result_data: dict | None = None
        last_error: str | None = None
        if self.system_prompt is not None:
            sys_prompt = self.system_prompt
            if few_shot:
                sys_prompt = sys_prompt + "\n\n" + FEWSHOT_TEMPLATE.format(few_shot=few_shot)
            if "{{table_directory}}" in sys_prompt:
                sys_prompt = sys_prompt.replace("{{table_directory}}", _table_directory())
        else:
            sys_prompt = build_system_prompt(few_shot=few_shot)
        messages = [
            ChatMessage(role="system", content=sys_prompt),
            *(history or []),  # 管理层装配的历史窗口（M3：跨轮指代）
            ChatMessage(role="user", content=task.question),
        ]

        for step in range(1, task.budget.max_steps + 1):
            # ④ 墙钟
            wc: GuardDecision = guard.check_wall_clock()
            if wc.verdict == "stop":
                return self._finalize(task, messages, "wall_clock", trace, usage, start, last_sql)

            # LLM 决策
            t0 = time.perf_counter()
            resp = self.llm.chat(messages, tools=self.registry.definitions())
            elapsed = round((time.perf_counter() - t0) * 1000)
            usage["prompt"] += resp.usage.prompt_tokens
            usage["completion"] += resp.usage.completion_tokens
            usage["total"] += resp.usage.total_tokens
            trace.append(TraceStep(step=step, action="llm", detail=resp.finish_reason or "",
                                   elapsed_ms=elapsed))
            self._emit("llm_done", step=step, finish_reason=resp.finish_reason)

            # 不变量 3：拒绝/过滤响应不执行工具
            if resp.tool_calls and not resp.should_execute_tools:
                content = resp.content or "（模型拒绝了本次请求，未执行任何工具）"
                self._emit("refusal", step=step)
                return self._answer(task, content, "final", trace, usage, start, last_sql, row_count, result_data)

            # 正常终止：无工具调用 → 最终回答
            if not resp.tool_calls:
                return self._answer(task, resp.content or "", "final", trace, usage, start,
                                    last_sql, row_count, result_data)

            # 不变量 1：先声明后回填
            messages.append(ChatMessage(role="assistant", content=resp.content, tool_calls=resp.tool_calls))

            stop_reason: str | None = None
            for call in resp.tool_calls:
                # ② 打转检测（在执行前拦截）
                decision = guard.record_action(call.name, call.arguments)
                if decision.verdict == "stop":
                    messages.append(ChatMessage(role="tool", tool_call_id=call.id, name=call.name,
                                                content=f"[防护终止] {decision.reason}"))
                    stop_reason = decision.reason
                    trace.append(TraceStep(step=step, action="guard", detail=decision.reason, ok=False))
                    self._emit("guard", verdict="stop", reason=decision.reason)
                    break
                if decision.verdict == "warn":
                    messages.append(ChatMessage(role="tool", tool_call_id=call.id, name=call.name,
                                                content=f"[防护警告] {decision.reason}"))
                    trace.append(TraceStep(step=step, action="guard", detail=decision.reason, ok=False))
                    self._emit("guard", verdict="warn", reason=decision.reason)
                    continue

                # 工具执行（错误即观察，不抛异常）
                t1 = time.perf_counter()
                result = self.registry.execute(call.name, call.arguments, ctx)
                elapsed_tool = round((time.perf_counter() - t1) * 1000)
                observation = str(result)
                self._emit("tool_result", name=call.name, is_error=result.is_error, elapsed_ms=elapsed_tool)

                # ③ 错误回喂限次（仅 execute_sql 计数）
                if result.is_error and call.name == "execute_sql":
                    d2 = guard.record_sql_error(call.arguments.get("sql", ""))
                    last_error = observation
                    if d2.verdict == "stop":
                        messages.append(ChatMessage(role="tool", tool_call_id=call.id, name=call.name,
                                                    content=observation))
                        content = (
                            f"无法完成本次取数：同一条 SQL 反复失败（已回喂修复 "
                            f"{task.budget.max_repair_same_sql} 次）。\n最后的错误：\n{observation}\n"
                            "建议：换一种问法，或转人工 / 提取数工单。"
                        )
                        trace.append(TraceStep(step=step, action="guard", detail="repair_limit", ok=False))
                        return self._answer(task, content, "repair_limit", trace, usage, start,
                                            last_sql=call.arguments.get("sql"))
                    if d2.verdict == "warn":
                        observation = f"{observation}\n[防护警告] {d2.reason}"
                elif not result.is_error and call.name == "execute_sql":
                    last_sql = call.arguments.get("sql")
                    try:
                        payload = json.loads(observation)
                        row_count = int(payload.get("row_count"))
                        result_data = {"columns": payload.get("columns", []),
                                       "rows": payload.get("rows", [])[:50]}
                    except (ValueError, TypeError):
                        row_count = None

                # 不变量 1：观察回填
                messages.append(ChatMessage(role="tool", tool_call_id=call.id, name=call.name,
                                            content=observation))
                trace.append(TraceStep(step=step, action=call.name, detail="error" if result.is_error else "ok",
                                       ok=not result.is_error, elapsed_ms=elapsed_tool))

            if stop_reason:
                return self._finalize(task, messages, stop_reason, trace, usage, start,
                                      last_sql, row_count, result_data)

        # ① 步数耗尽
        return self._finalize(task, messages, "max_steps", trace, usage, start, last_sql, row_count, result_data)

    # ---------- 终止路径 ----------

    def _finalize(self, task: TaskRequest, messages: list[ChatMessage], reason: str,
                  trace: list[TraceStep], usage: dict, start: float, last_sql: str | None,
                  row_count: int | None = None, result_data: dict | None = None) -> Answer:
        """①②④ 类终止：发一次无工具请求强制总结；失败退到模板文案（nanobot 模式）。"""
        messages.append(ChatMessage(role="user", content=SUMMARY_REQUEST))
        try:
            resp = self.llm.chat(messages, tools=None)
            usage["prompt"] += resp.usage.prompt_tokens
            usage["completion"] += resp.usage.completion_tokens
            usage["total"] += resp.usage.total_tokens
            content = resp.content or FALLBACK_SUMMARY
        except LLMError:
            content = FALLBACK_SUMMARY
        return self._answer(task, content, f"{reason}_summary" if reason in ("max_steps", "wall_clock") else reason,
                            trace, usage, start, last_sql, row_count, result_data)

    def _answer(self, task: TaskRequest, content: str, stop_reason: str, trace: list[TraceStep],
                usage: dict, start: float, last_sql: str | None,
                row_count: int | None = None, result_data: dict | None = None) -> Answer:
        self._emit("final", stop_reason=stop_reason)
        return Answer(
            question=task.question,
            content=content,
            sql=last_sql,
            row_count=row_count,
            result=result_data,
            stop_reason=stop_reason,
            steps=trace,
            prompt_tokens=usage["prompt"],
            completion_tokens=usage["completion"],
            total_tokens=usage["total"],
            elapsed_ms=round((time.perf_counter() - start) * 1000),
        )
