"""Milvus 向量存储适配层（ADR-008 D7）：本机模拟内网向量平台 / 生产内网直连。

- 客户端：pymilvus MilvusClient——同一 API 覆盖 Milvus Lite（内嵌，Linux/macOS）
  与 Standalone（Docker/内网生产）；Windows 上 Lite 不可用，用 deploy/ 下的
  docker-compose 起 standalone 模拟内网（T2S_MILVUS_URI=http://127.0.0.1:19530）。
- fail-fast：构造即探活（list_collections），连不上抛 MilvusUnavailableError，
  上层捕获后降级到 SQLite 自建向量（ADR-007）。
- 集按用途分集合：qa_vectors（样例库）/ saved_vectors（结果表格记忆）。
"""
from __future__ import annotations

from typing import Any


class MilvusUnavailableError(RuntimeError):
    pass


class MilvusVectorStore:
    def __init__(self, uri: str, collection: str, dim: int,
                 token: str | None = None) -> None:
        try:
            from pymilvus import MilvusClient
        except ImportError as e:  # pragma: no cover
            raise MilvusUnavailableError(
                f"需要 pymilvus（pip install pymilvus）: {e}") from e
        self.collection = collection
        self.dim = dim
        try:
            self.client = MilvusClient(uri=uri, token=token) if token else MilvusClient(uri=uri)
            self.client.list_collections()  # 探活：MilvusClient 连接可能惰性，显式验证
        except Exception as e:  # noqa: BLE001 —— 连接失败统一转降级信号
            raise MilvusUnavailableError(f"Milvus 连接失败（{uri}）: {e}") from e
        if not self.client.has_collection(collection):
            self.client.create_collection(
                collection_name=collection,
                dimension=dim,
                metric_type="COSINE",
                auto_id=False,
            )

    def upsert(self, item_id: str, vector: list[float], text: str) -> None:
        self.client.insert(self.collection, {"id": item_id, "vector": vector, "text": text})

    def search(self, vector: list[float], top_k: int = 3) -> list[dict[str, Any]]:
        hits = self.client.search(self.collection, data=[vector], limit=top_k,
                                  output_fields=["text"])
        return [{"id": h["id"], "score": h["distance"], "text": h["entity"].get("text", "")}
                for h in (hits[0] if hits else [])]

    def delete(self, item_id: str) -> None:
        self.client.delete(self.collection, ids=[item_id])
