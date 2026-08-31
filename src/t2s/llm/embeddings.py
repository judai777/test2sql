"""Embedding 客户端：OpenAI 兼容 /embeddings 协议。

选型说明（ADR-007）：不用 chromadb——重依赖且默认嵌入模型需境外下载；
本项目向量量级 <1k，SQLite 存向量 + Python 余弦足够。未配置模型时上层降级为关键词检索。
"""
from __future__ import annotations

from typing import Any

import httpx

from t2s.config import EmbeddingConfig


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    def __init__(self, config: EmbeddingConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._http = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(connect=10.0, read=config.timeout_s, write=30.0, pool=10.0),
            transport=transport,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化，返回与输入顺序一致的向量列表。失败抛 EmbeddingError（上层降级）。"""
        if not texts:
            return []
        try:
            resp = self._http.post("/embeddings", json={"model": self.config.model, "input": texts})
        except httpx.TransportError as e:
            raise EmbeddingError(f"Embedding 网络错误: {e}") from e
        if resp.status_code != 200:
            raise EmbeddingError(f"Embedding 请求失败 HTTP {resp.status_code}: {resp.text[:200]}")
        data: list[dict[str, Any]] = resp.json().get("data") or []
        if len(data) != len(texts):
            raise EmbeddingError(f"Embedding 返回数量不符: {len(data)} != {len(texts)}")
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        vectors = [d.get("embedding") for d in ordered]
        if any(not isinstance(v, list) for v in vectors):
            raise EmbeddingError("Embedding 响应缺少 embedding 字段")
        return vectors  # type: ignore[return-value]

    def close(self) -> None:
        self._http.close()
