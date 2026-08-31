"""记忆服务（管理层，零业务逻辑外溢）：样例库/口径库的检索与写入（PRD FR-8）。

检索双通道（ADR-007）：
- Embedding 已配置 → 查询向量 + 余弦 top-k（失败自动降级关键词）
- 未配置/失败   → 关键词 bigram 出现次数打分
双通道都不命中等价于空字符串——组装层跳过注入，绝不阻塞主流程（DB-GPT 降级意识）。
"""
from __future__ import annotations

import math
from typing import Any

from t2s.llm import EmbeddingClient
from t2s.models.records import MetricDoc, QAPair
from t2s.storage import MemoryStore
from t2s.utils.text import bigram_score


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class MemoryService:
    def __init__(self, store: MemoryStore, embedder: EmbeddingClient | None = None,
                 retrieval: Any | None = None) -> None:
        self.store = store
        self.embedder = embedder
        self.retrieval = retrieval  # manager.RetrievalService（M7 混合检索），可选

    # ---------- 写入 ----------

    def record_success(self, question: str, sql: str) -> None:
        """双通过（validate+execute）后由路由层调用；向量化失败静默降级为仅关键词可检索。"""
        embedding = None
        if self.embedder is not None:
            try:
                embedding = self.embedder.embed([question])[0]
            except Exception:  # noqa: BLE001 —— 记忆写入失败不阻塞主流程
                embedding = None
        self.store.add_pair(question, sql, embedding)
        # 表格与字段复用统计（ADR-008 D4）：sqlglot 反解 SQL
        try:
            import sqlglot
            from sqlglot import exp
            stmt = sqlglot.parse_one(sql, read="sqlite")
            tables = sorted({t.name for t in stmt.find_all(exp.Table)})
            fields = sorted({c.name for c in stmt.find_all(exp.Column)
                             if c.name and c.name != "*"})
            self.store.bump_usage(tables, fields)
        except Exception:  # noqa: BLE001 —— 统计失败不影响主流程
            pass

    def forget_pair(self, pair_id: int) -> bool:
        """用户点踩删除（PRD FR-8）。"""
        return self.store.remove_pair(pair_id)

    # ---------- 确认制与结果表格记忆（ADR-008 D2，M8） ----------

    def confirm_pair(self, pair_id: int) -> bool:
        """用户确认：候选 → 正式库（检索可见）。"""
        return self.store.confirm_pair(pair_id)

    def candidate_pairs(self) -> list:
        return self.store.candidate_pairs()

    def save_result(self, title: str, question: str, sql: str,
                    columns: list, rows: list) -> int:
        """用户主动保存查询结果表（结果表格记忆）；向量化失败静默降级。"""
        embedding = None
        if self.embedder is not None:
            try:
                embedding = self.embedder.embed([f"{title} {question}"])[0]
            except Exception:  # noqa: BLE001
                embedding = None
        return self.store.save_result(title, question, sql, columns, rows, embedding)

    def remove_result(self, result_id: int) -> bool:
        return self.store.remove_result(result_id)

    def all_results(self) -> list[dict]:
        return self.store.all_results()

    def retrieve_saved_results(self, question: str, top_k: int = 1) -> str:
        """跨会话复用：相似的用户保存结果表（标题/问题词面 + 向量）。"""
        results = self.store.all_results()
        if not results:
            return ""
        ranked = self._rank(question, results, lambda r: f"{r['title']} {r['question']}")[:top_k]
        if all(bigram_score(question, f"{r['title']} {r['question']}") == 0
               and r["embedding"] is None for r in ranked):
            return ""
        return "\n\n".join(
            f"【{r['title']}】问题：{r['question']}\nSQL：\n{r['sql']}" for r in ranked)

    # ---------- 检索 ----------

    def _embed_query(self, question: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            return self.embedder.embed([question])[0]
        except Exception:  # noqa: BLE001
            return None

    def _rank(self, question: str, items: list, text_of) -> list:
        """items 按 (向量余弦 | 关键词分) 降序；无 embedding 的行走关键词。"""
        qvec = self._embed_query(question)
        if qvec is not None and any(item.embedding for item in items):

            def score(item):
                if item.embedding:
                    return _cosine(qvec, item.embedding)
                return bigram_score(question, text_of(item)) / 1000.0  # 不同量纲，混合排序时垫底权重

            return sorted(items, key=lambda it: (-score(it), getattr(it, "id", 0)))
        return sorted(items, key=lambda it: (-bigram_score(question, text_of(it)),
                                             getattr(it, "id", 0)))

    def retrieve_few_shot(self, question: str, top_k: int = 3) -> str:
        pairs: list[QAPair] = self.store.all_pairs()
        if not pairs:
            return ""
        # M7：混合检索可用时委托（关键词/语义并联 RRF + 精确命中/近因重排）；
        # 否则走 v1 互斥降级通道。
        if self.retrieval is not None:
            top = self.retrieval.rank_pairs(question, pairs, top_k)
        else:
            top = self._rank(question, pairs, lambda p: f"{p.question} {p.sql}")[:top_k]
            # 关键词通道下全零分等于未命中
            if all(bigram_score(question, f"{p.question} {p.sql}") == 0 for p in top) and top[0].embedding is None:
                return ""
        return "\n\n".join(f"问：{p.question}\nSQL：\n{p.sql}" for p in top)

    def retrieve_metrics(self, question: str, top_k: int = 2) -> str:
        docs: list[MetricDoc] = self.store.all_metrics()
        if not docs:
            return ""
        top = self._rank(question, docs, lambda d: f"{d.title} {d.content}")[:top_k]
        if all(bigram_score(question, f"{d.title} {d.content}") == 0 for d in top) and top[0].embedding is None:
            return ""
        return "\n\n".join(f"【{d.title}】{d.content}" for d in top)

    def build_memory_context(self, question: str) -> str:
        """组装注入引擎 system prompt 的记忆块（few_shot 参数）。"""
        parts = []
        few_shot = self.retrieve_few_shot(question)
        if few_shot:
            parts.append(f"### 相似历史问答\n{few_shot}")
        metrics = self.retrieve_metrics(question)
        if metrics:
            parts.append(f"### 业务口径\n{metrics}")
        saved = self.retrieve_saved_results(question)
        if saved:
            parts.append(f"### 用户保存的历史结果（口径与写法可复用）\n{saved}")
        return "\n\n".join(parts)
