"""权限初检 + 表级白名单（ADR-008 D6：权限从提示词软约束升级为代码硬约束）。

硬约束生效点在 execute_sql/validate_sql：sqlglot 提取实际访问表集合，
与本注册表下发的 allowed_tables 做代码级比对，越权即不可重试拒绝。
真正的兜底仍是 SQLite mode=ro 只读连接。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""
    mode: str = "readonly_all"
    allowed_tables: frozenset[str] | None = None   # None = 全表只读；否则白名单


class PermissionRegistry:
    """per-user 表级白名单注册表。

    user_tables: {user_id: frozenset(表名) | None}；None/缺省 = 全表只读。
    一期只读账号语义不变；本类只决定"允许看见哪些表"。
    """

    def __init__(self, user_tables: dict[str, frozenset[str] | None] | None = None) -> None:
        self._user_tables = dict(user_tables or {})

    def check(self, user_id: str) -> PermissionDecision:
        tables = self._user_tables.get(user_id)
        if tables is None:
            return PermissionDecision(allowed=True, reason="默认只读权限（readonly_all）",
                                      allowed_tables=None)
        if not tables:
            return PermissionDecision(allowed=False,
                                      reason="该用户未授予任何数据表访问权限",
                                      mode="restricted")
        return PermissionDecision(allowed=True, reason="受限白名单权限",
                                  mode="readonly_whitelist",
                                  allowed_tables=frozenset(t.lower() for t in tables))
