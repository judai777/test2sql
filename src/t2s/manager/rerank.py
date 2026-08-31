"""重排器（ADR-008 D5）：召回之后的相关性精排。

接口化三实现：
- HeuristicReranker：复用频次 + 精确命中加权，零依赖（开发机/CI 默认）
- CrossEncoderReranker：bge-reranker ONNX 本地推理（内网生产，M9 交付，需模型路径）
- LLM 精排：否决（内网合规数据出域 + 共享 GPU 成本，见 ADR-008 D5）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from t2s.utils.text import bigram_score


@dataclass
class RankedItem:
    """重排输入：任意载荷 + 召回阶段的基础分（0~1 归一）+ 可选元数据。"""

    payload: Any
    base_score: float
    text: str                      # 用于精确命中判断的文本（小写比较由实现内做）
    meta: dict = field(default_factory=dict)


class Reranker(Protocol):
    def rerank(self, query: str, items: list[RankedItem], top_k: int) -> list[RankedItem]: ...


@dataclass
class HeuristicReranker:
    """启发式重排：final = base × (1 + α·log1p(复用)) + β·精确命中 + γ·近因。

    纯确定性代码，可单测、可解释；usage_hits 由调用方从 schema_usage 读出
    （item.meta["usage_hits"]，缺省 0）。
    """

    alpha: float = 0.3    # 复用频次权重
    beta: float = 0.15    # 精确命中权重
    gamma: float = 0.1    # 近因权重（7 天内）

    def rerank(self, query: str, items: list[RankedItem], top_k: int) -> list[RankedItem]:
        q = query.lower()
        scored: list[tuple[float, int, RankedItem]] = []
        for i, item in enumerate(items):
            score = item.base_score
            hits = item.meta.get("usage_hits", 0)
            if hits:
                score *= 1 + self.alpha * math.log1p(hits)
            hit_text = item.meta.get("exact_text", item.text).lower()
            if hit_text and (hit_text in q or q in hit_text):
                score += self.beta
            if item.meta.get("recent"):
                score += self.gamma
            scored.append((score, -i, item))          # -i 稳定排序：同分保持召回序
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [item for _, _, item in scored[:max(1, top_k)]]


class CrossEncoderReranker:
    """bge-reranker ONNX 本地推理（内网生产形态，M9 交付）。

    依赖 onnxruntime + tokenizers（可选 extras `.[rerank]`），模型文件由内网分发，
    通过 T2S_RERANK_MODEL_PATH 指定——代码零自动下载（ADR-007/008）。
    未配置时抛 RuntimeError，由调用方降级到 HeuristicReranker。
    """

    def __init__(self, model_path: str, max_length: int = 512) -> None:
        try:
            import onnxruntime  # noqa: F401  惰性导入：未装 extras 时不影响系统
            from tokenizers import Tokenizer
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                f"CrossEncoderReranker 需要 rerank extras（pip install '.[rerank]'）: {e}") from e
        self.tokenizer = Tokenizer.from_file(f"{model_path.rstrip('/')}/tokenizer.json")
        self.session = onnxruntime.InferenceSession(
            f"{model_path.rstrip('/')}/model.onnx", providers=["CPUExecutionProvider"])
        self.max_length = max_length

    def rerank(self, query: str, items: list[RankedItem], top_k: int) -> list[RankedItem]:
        pairs = [(query, it.text) for it in items]
        enc = self.tokenizer.encode_batch(pairs)
        feeds = {
            "input_ids": [e.ids[: self.max_length] for e in enc],
            "attention_mask": [e.attention_mask[: self.max_length] for e in enc],
        }
        logits = self.session.run(None, feeds)[0]
        order = sorted(range(len(items)), key=lambda i: -float(logits[i][0]))
        return [items[i] for i in order[:max(1, top_k)]]
