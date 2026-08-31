"""ask_user 工具：指标口径/时间范围等实质歧义时的澄清通道。"""
from __future__ import annotations

from t2s.tools.base import Tool, ToolContext, ToolResult

_FALLBACK = "（当前环境无交互终端）请基于合理假设继续作答，并在答案开头显式声明所做假设。"


class AskUser(Tool):
    name = "ask_user"
    description = (
        "当问题的指标口径、时间范围或过滤条件存在无法从表结构推断的实质歧义时，"
        "向用户发起一次澄清提问。不要为可从上下文合理推断的细节提问；每轮最多使用 2 次（引擎强制）。"
    )
    parameters = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "question": {"type": "string", "description": "面向业务人员的、具体且可一句话回答的问题"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选的候选项（如口径选项），无则省略",
            },
        },
        "required": ["question"],
    }

    def execute(self, ctx: ToolContext, question: str, options: list[str] | None = None) -> ToolResult:
        if ctx.ask_user is None:
            return ToolResult.ok(_FALLBACK)
        answer = ctx.ask_user(question, options)
        return ToolResult.ok(f"用户答复: {answer}")
