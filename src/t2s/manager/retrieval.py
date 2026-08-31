"""混合检索（ADR-008 D3/D4/D5）：关键词主导（0.7）+ 语义辅助（0.3）RRF 融合 → 启发式重排。

依据：金融 schema linking 需要表名/字段名精确定位，业务术语词面命中精度最高；
语义只兜模糊描述（失败模式不对称，精度优先）。表语料由调用方注入（元数据语料质量
是关键词通道的隐含前提，业务别名词表从评测失败案例增量收集）。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from t2s.llm import EmbeddingClient
from t2s.storage import MemoryStore
from t2s.utils.text import bigram_score

KEYWORD_WEIGHT = 0.7
SEMANTIC_WEIGHT = 0.3
RRF_K = 60


def rrf_fusion(rankings: list[tuple[list[Any], float]], k: int = RRF_K) -> dict[Any, float]:
    """加权 Reciprocal Rank Fusion：rankings = [(按序键列表, 权重)]。"""
    scores: dict[Any, float] = {}
    for keys, weight in rankings:
        for i, key in enumerate(keys):
            scores[key] = scores.get(key, 0.0) + weight / (k + i + 1)
    return scores


class RetrievalService:
    """表召回 + 样例重排的统一入口（管理层确定性治理，零业务逻辑）。"""

    def __init__(self, tables: list[tuple[str, str]],
                 store: MemoryStore | None = None,
                 embedder: EmbeddingClient | None = None) -> None:
        self.tables = tables                     # (表名, 语料)
        self.store = store
        self.embedder = embedder
        self._table_vecs: list[list[float]] | None = None

    # ---------- 语义通道（辅助，失败静默降级为纯关键词） ----------

    def _embed_query(self, query: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            return self.embedder.embed([query])[0]
        except Exception:  # noqa: BLE001
            return None

    def _semantic_table_ranking(self, query: str) -> list[str] | None:
        if self.embedder is None:
            return None
        try:
            if self._table_vecs is None:
                self._table_vecs = self.embedder.embed([corpus for _, corpus in self.tables])
            qv = self._embed_query(query)
            if qv is None:
                return None
            scored = [(name, _cosine(qv, vec)) for (name, _), vec in zip(self.tables, self._table_vecs)]
            return [name for name, _ in sorted(scored, key=lambda x: -x[1])]
        except Exception:  # noqa: BLE001
            return None

    # ---------- 表召回：RRF 融合 + 复用/精确命中重排 ----------

    def search_tables(self, query: str, top_k: int = 3) -> list[tuple[str, float]]:
        ql = query.lower()
        kw = sorted(self.tables,
                    key=lambda t: -bigram_score(query, f"{t[0]} {t[1]}"))
        rankings: list[tuple[list[str], float]] = [([n for n, _ in kw], KEYWORD_WEIGHT)]
        sem = self._semantic_table_ranking(query)
        if sem:
            rankings.append((sem, SEMANTIC_WEIGHT))
        scores = rrf_fusion(rankings)

        usage = self.store.usage_map("table") if self.store else {}
        reranked = []
        for name, base in scores.items():
            hits = usage.get(name.lower(), 0)
            score = base * (1 + 0.3 * math.log1p(hits))     # 复用频次（D4）
            if name.lower() in ql:                           # 精确命中表名（强信号）
                score += 0.25
            reranked.append((name, score))
        reranked.sort(key=lambda x: (-x[1], x[0]))
        return reranked[:max(1, top_k)]

    # ---------- 样例重排：混合召回 + 精确命中 + 近因 ----------

    def rank_pairs(self, query: str, pairs: list, top_k: int = 3) -> list:
        """pairs: QAPair 列表（含 question/sql/updated_at/embedding）。返回重排后的前 top_k。"""
        if not pairs:
            return []
        ql = query.lower()
        kw = sorted(pairs, key=lambda p: -bigram_score(query, f"{p.question} {p.sql}"))
        rankings: list[tuple[list[Any], float]] = [([p.id for p in kw], KEYWORD_WEIGHT)]
        qv = self._embed_query(query)
        if qv is not None and any(p.embedding for p in pairs):
            sem = sorted(pairs, key=lambda p: -_cosine(qv, p.embedding or []))
            rankings.append(([p.id for p in sem], SEMANTIC_WEIGHT))
        scores = rrf_fusion(rankings)

        now = datetime.now()
        for p in pairs:
            if p.id not in scores:
                continue
            pq = p.question.lower()
            if pq and (pq in ql or ql in pq):                # 精确命中问句
                scores[p.id] += 0.2
            try:
                days = (now - datetime.fromisoformat(p.updated_at)).days
                scores[p.id] += 0.1 * math.exp(-max(days, 0) / 30)   # 近因衰减
            except (ValueError, TypeError):
                pass
        ordered = sorted(pairs, key=lambda p: (-scores.get(p.id, 0.0), p.id))
        return ordered[:max(1, top_k)]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
