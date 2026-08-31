"""异步会话摘要（ADR-008 D2：短期记忆 = 滑动窗口 + 异步摘要）。

窗口外的历史不直接丢弃，而是后台线程滚动摘要进 sessions.summary；
下一次 build_history 以"[此前对话摘要]"注入——长会话跨轮指代不丢上下文。
失败静默（摘要缺席时窗口裁剪照常工作，DB-GPT 降级意识）。
"""
from __future__ import annotations

import threading

from t2s.executor.prompts import SUMMARY_SYSTEM
from t2s.llm import ChatMessage, LLMClient, LLMError
from t2s.manager.context import CONTEXT_MAX_MESSAGES
from t2s.storage import SessionStore


class SessionSummarizer:
    def __init__(self, llm: LLMClient, store: SessionStore,
                 window: int = CONTEXT_MAX_MESSAGES,
                 async_mode: bool = True) -> None:
        self.llm = llm
        self.store = store
        self.window = window
        self.async_mode = async_mode

    def pending(self, session_id: str) -> bool:
        """水位之后、窗口之外还有未摘要消息 → 需要摘要。"""
        _, until = self.store.get_summary(session_id)
        total = self.store.count_since(session_id, 0)
        unsummarized = self.store.count_since(session_id, until)
        return unsummarized > self.window

    def maybe_summarize_async(self, session_id: str) -> None:
        """异步触发（守护线程，fire-and-forget；失败静默）。"""
        if not self.pending(session_id):
            return
        if self.async_mode:
            threading.Thread(target=self._safe_summarize, args=(session_id,),
                             daemon=True).start()
        else:
            self._safe_summarize(session_id)

    def _safe_summarize(self, session_id: str) -> None:
        try:
            self.summarize(session_id)
        except Exception:  # noqa: BLE001 —— 摘要失败不阻塞主流程
            pass

    def summarize(self, session_id: str) -> str | None:
        """同步摘要：把窗口外消息并入滚动摘要。返回新摘要或 None。"""
        _, until = self.store.get_summary(session_id)
        older = self.store.fetch_since(session_id, until, exclude_last=self.window)
        if not older:
            return None
        transcript = "\n".join(f"{m.role}: {m.content[:300]}" for m in older)
        prior, _ = self.store.get_summary(session_id)
        prompt = f"[既有摘要]\n{prior}\n\n[新增对话]\n{transcript}" if prior else transcript
        resp = self.llm.chat(
            [ChatMessage(role="system", content=SUMMARY_SYSTEM),
             ChatMessage(role="user", content=prompt)],
            temperature=0.1,
        )
        summary = (resp.content or "").strip()
        if not summary:
            return None
        last_id = older[-1].id
        self.store.save_summary(session_id, summary, until_id=last_id)
        return summary
