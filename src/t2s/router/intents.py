"""路由层：危险请求前置拦截 + 意图分类（PRD FR-6）。"""
from __future__ import annotations

import re

from t2s.llm import ChatMessage, LLMClient
from t2s.router.prompts import INTENT_SYSTEM

# 已知限制（CHG-004）：宽松匹配宁可误拦不可漏拦（fail-safe）——误拦的代价是
# 一次拒绝文案，漏拦的代价是安全事件。误报样本请记入 CHG 持续收敛词表。
_DANGEROUS_RE = re.compile(
    r"(删除|删掉|清除|清空|抹掉|插入|写入|建表|删表|加字段|授权|改成|"
    r"drop\s+table|delete\s+from|insert\s+into|update\s+\S+\s+set|truncate|alter\s+table|grant\s)",
    re.IGNORECASE,
)


def is_dangerous(text: str) -> bool:
    return bool(_DANGEROUS_RE.search(text))


def classify_intent(llm: LLMClient, question: str) -> str:
    """一次轻量调用分 data_query / chat；解析失败回退 data_query（产品主路径）。"""
    try:
        resp = llm.chat(
            [ChatMessage(role="system", content=INTENT_SYSTEM),
             ChatMessage(role="user", content=question)],
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001 —— 分类失败不阻塞主流程
        return "data_query"
    m = re.search(r'"intent"\s*:\s*"([a-z_]+)"', resp.content or "")
    intent = m.group(1) if m else ""
    return intent if intent in ("data_query", "chat") else "data_query"
