"""工具契约（nanobot `tools/base.py` 模式的简化移植）。

- schema 即代码：parameters 既是发给 LLM 的 function schema，也是本地校验依据。
- 错误即观察：工具失败绝不抛异常到引擎，而是包成带修复提示的错误观察（ADR-001 D6 ③ 的地基）。
- 参数先 cast 后 validate：兼容"LLM 把参数当字符串传"这一高频失败模式。
"""
from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

RETRY_HINT = "\n\n[请分析以上错误，并尝试不同的方法。]"


class ToolResult(str):
    """字符串子类 + is_error 标志：工具输出天然可回填为 role=tool 观察。"""

    def __new__(cls, value: str, is_error: bool = False) -> "ToolResult":
        obj = super().__new__(cls, value)
        obj.is_error = is_error
        return obj

    @classmethod
    def ok(cls, value: str) -> "ToolResult":
        return cls(value, is_error=False)

    @classmethod
    def error(cls, value: str) -> "ToolResult":
        return cls(value, is_error=True)


AskUserFn = Callable[[str, list[str] | None], str]


@dataclass
class ToolContext:
    """引擎注入给每个工具执行的运行上下文。"""

    db_path: Path
    sql_timeout_s: float = 30.0
    sql_row_limit: int = 100
    # M1：由驱动器（REPL/Web）注入；未注入时 ask_user 工具降级为"按假设继续"
    ask_user: AskUserFn | None = None
    # M7：混合检索服务（manager.RetrievalService，鸭子类型注入）；缺省走关键词兜底
    retriever: Any = None
    # M7：权限硬约束（ADR-008 D6）——用户可访问表白名单；None = 全表只读
    allowed_tables: frozenset[str] | None = None
    # 供扩展：session 等
    extra: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema，object 类型

    @abstractmethod
    def execute(self, ctx: ToolContext, **params) -> ToolResult: ...

    def to_schema(self) -> dict[str, Any]:
        """OpenAI function-calling 格式；deepcopy 防止调用方改坏共享 schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(self.parameters),
            },
        }

    # ---------- 参数 cast + 校验 ----------

    def cast_and_validate(self, params: Any) -> tuple[dict[str, Any], str | None]:
        if not isinstance(params, dict):
            return {}, f"参数必须是 JSON 对象，收到 {type(params).__name__}。"
        errors: list[str] = []
        casted = self._cast_object(params, self.parameters, "$", errors)
        if errors:
            return {}, "参数错误：" + "；".join(errors)
        return casted, None

    def _cast_object(self, obj: dict, schema: dict, path: str, errors: list[str]) -> dict[str, Any]:
        props: dict = schema.get("properties") or {}
        required = schema.get("required") or []
        if schema.get("additionalProperties") is False:
            for key in obj:
                if key not in props:
                    errors.append(f"{path}.{key} 不是合法参数（允许的参数: {', '.join(props) or '无'}）")
        out: dict[str, Any] = {}
        for key, spec in props.items():
            if key in obj:
                # 显式 null 的可选参数视同省略——LLM 高频行为（MCP 往返/工具调用实测），
                # 否则可选参数的 null 会触发类型校验失败
                if obj[key] is None and key not in required:
                    continue
                out[key] = self._cast_value(obj[key], spec, f"{path}.{key}", errors)
            elif key in required:
                errors.append(f"{path}.{key} 是必填参数")
        return out

    def _cast_value(self, value: Any, schema: dict, path: str, errors: list[str]) -> Any:
        t = schema.get("type")
        value = self._coerce(value, t)
        if t == "object" and isinstance(value, dict):
            return self._cast_object(value, schema, path, errors)
        if t == "array":
            if not isinstance(value, list):
                errors.append(f"{path} 应为数组")
                return value
            item_schema = schema.get("items") or {}
            return [self._cast_value(v, item_schema, f"{path}[{i}]", errors) for i, v in enumerate(value)]
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path} 必须是 {'/'.join(map(str, schema['enum']))} 之一，收到 {value!r}")
        self._check_type(value, t, path, errors)
        return value

    @staticmethod
    def _coerce(value: Any, t: str | None) -> Any:
        """宽容矫正："42"→42、"true"→True、int→float。"""
        if t == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
        if t == "number" and isinstance(value, (str, int)) and not isinstance(value, bool):
            try:
                return float(value)
            except ValueError:
                return value
        if t == "boolean" and isinstance(value, str):
            if value.lower() == "true":
                return True
            if value.lower() == "false":
                return False
        if t == "number" and isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        return value

    @staticmethod
    def _check_type(value: Any, t: str | None, path: str, errors: list[str]) -> None:
        checks = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "null": lambda v: v is None,
        }
        if t in checks and not checks[t](value):
            errors.append(f"{path} 类型应为 {t}，收到 {type(value).__name__}")
