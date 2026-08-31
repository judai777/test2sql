"""路由层服务：应用入口编排（依赖方向 ui → router → manager → executor，ADR-005）。

handle() 是三层架构的串接点：危险拦截 → 意图分流 → 上下文装配 → 引擎执行 → 会话回写 → 审计。
"""
from __future__ import annotations

import time
from dataclasses import replace

from t2s.llm import ChatMessage, LLMError
from t2s.manager.context import build_history
from t2s.models.records import AuditEntry
from t2s.models.task import Answer, TaskRequest
from t2s.router.intents import classify_intent, is_dangerous
from t2s.router.permissions import PermissionRegistry
from t2s.router.prompts import CHAT_SYSTEM, DANGEROUS_REPLY
from t2s.storage import AuditLog, SessionStore
from t2s.tools import ToolContext


class RouterService:
    def __init__(self, llm, engine, session_store: SessionStore, audit: AuditLog,
                 permissions: PermissionRegistry | None = None, memory=None,
                 summarizer=None) -> None:
        self.llm = llm
        self.engine = engine
        self.session_store = session_store
        self.audit = audit
        self.permissions = permissions or PermissionRegistry()
        self.memory = memory  # manager.MemoryService（M4），可选
        self.summarizer = summarizer  # manager.SessionSummarizer（M8），可选

    def handle(self, question: str, session_id: str = "default", user_id: str = "dev",
               ctx: ToolContext | None = None) -> Answer:
        start = time.perf_counter()

        # ⓪ 权限初检（ADR-008 D6：白名单随请求注入执行层，硬约束在 SQL 工具内比对）
        perm = self.permissions.check(user_id)
        if not perm.allowed:
            return self._finish(question, session_id, user_id, "blocked_permission",
                                Answer(question=question, content=f"权限不足：{perm.reason}",
                                       stop_reason="blocked_permission"))
        if ctx is not None and perm.allowed_tables is not None:
            ctx = replace(ctx, allowed_tables=perm.allowed_tables)

        # ① 危险请求前置拦截（不花 LLM 调用，fail-safe）
        if is_dangerous(question):
            return self._finish(question, session_id, user_id, "dangerous",
                                Answer(question=question, content=DANGEROUS_REPLY,
                                       stop_reason="blocked_dangerous"))

        # ② 意图分流
        intent = classify_intent(self.llm, question)
        if intent == "chat":
            try:
                resp = self.llm.chat([ChatMessage(role="system", content=CHAT_SYSTEM),
                                      ChatMessage(role="user", content=question)])
            except LLMError as e:
                return self._finish(question, session_id, user_id, "chat",
                                    Answer(question=question, stop_reason="error",
                                           content=f"LLM 服务暂时不可用：{e}"))
            return self._finish(question, session_id, user_id, "chat",
                                Answer(question=question, content=resp.content or "",
                                       stop_reason="final"))

        # ③ 取数主路径：记忆检索 → 会话落盘 → 上下文装配 → 引擎 → 回写会话 → 记忆沉淀
        if ctx is None:
            raise ValueError("取数请求需要 ToolContext（业务库连接参数）")
        memory_block = self.memory.build_memory_context(question) if self.memory else ""
        self.session_store.append(session_id, "user", question)
        history = build_history(self.session_store, session_id)
        try:
            ans = self.engine.run(
                TaskRequest(question=question, session_id=session_id, user_id=user_id),
                ctx, history=history, few_shot=memory_block)
        except LLMError as e:
            ans = Answer(question=question, stop_reason="error",
                         content=f"LLM 服务暂时不可用：{e}")
        self.session_store.append(session_id, "assistant", ans.content, sql=ans.sql)
        # 双通过自动沉淀（PRD FR-8）：final 且有 SQL ⇒ validate+execute 都已通过
        if self.memory and ans.stop_reason == "final" and ans.sql:
            self.memory.record_success(question, ans.sql)
        # 异步摘要（ADR-008 D2）：窗口外历史滚动摘要，守护线程失败静默
        if self.summarizer is not None:
            self.summarizer.maybe_summarize_async(session_id)
        return self._finish(question, session_id, user_id, intent, ans)

    def _finish(self, question: str, session_id: str, user_id: str, intent: str,
                ans: Answer) -> Answer:
        """审计必录（PRD FR-12）：即使是拦截与失败也要留下痕迹。"""
        self.audit.log(AuditEntry.now_entry(
            question=question, session_id=session_id, user_id=user_id, intent=intent,
            stop_reason=ans.stop_reason, sql=ans.sql, row_count=ans.row_count,
            steps=len(ans.steps), total_tokens=ans.total_tokens, elapsed_ms=ans.elapsed_ms,
            content=ans.content))
        return ans
