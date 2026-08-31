"""任务与结果模型：引擎的输入输出协议。

Budget 的默认值即 PRD FR-4 的四层防护参数；护栏不可放松（AGENTS.md §4）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Budget(BaseModel):
    """单次取数任务的预算（四层防护参数化，ADR-001 D6）。"""

    max_steps: int = 12                  # ① 步数上限（耗尽 → 无工具强制总结）
    turn_timeout_s: float = 300.0        # ④ 单 turn 墙钟（含 LLM 限流重试等待，智谱高峰实测需 300s）
    repeat_warn_at: int = 2              # ② 同动作连续 N 次注入警告
    repeat_stop_at: int = 3              # ② 重复/振荡累计 N 次强停
    max_repair_same_sql: int = 2         # ③ 同一 SQL 错误回喂上限（第 3 次诚实失败）


class TaskRequest(BaseModel):
    """路由层（M3）→ 执行层的结构化任务请求。"""

    session_id: str = "default"
    user_id: str = "dev"
    question: str
    mode: Literal["react", "fast"] = "react"        # fast 为二期快路径（强制计划模式）
    permission: Literal["readonly_all"] = "readonly_all"  # M3 扩展 per-user 白名单
    budget: Budget = Field(default_factory=Budget)


class TraceStep(BaseModel):
    """可观测性：每一步的轨迹（NFR-5）。"""

    step: int
    action: str                  # "llm" | 工具名 | "guard" | "final"
    detail: str = ""
    ok: bool = True
    elapsed_ms: int = 0


class Answer(BaseModel):
    """引擎输出：最终回答 + 完整轨迹。"""

    question: str
    content: str                 # 面向用户的最终回答
    sql: str | None = None       # 最后一次成功执行的 SQL（透明展示）
    row_count: int | None = None # 该 SQL 返回的行数（审计用）
    result: dict | None = None   # 结果集样本 {columns, rows≤50}（UI 表格/图表渲染用）
    stop_reason: str             # final | max_steps | max_steps_summary | repeat_stop | repair_limit | wall_clock_summary | blocked_dangerous
    steps: list[TraceStep] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_ms: int = 0
