"""Milvus 适配层测试：门禁 = 设置 T2S_MILVUS_URI 且服务可达（本机 Docker standalone 或内网集群）。"""
from __future__ import annotations

import os

import pytest

from t2s.storage.milvus_store import MilvusUnavailableError, MilvusVectorStore

_URI = os.environ.get("T2S_MILVUS_URI", "")


@pytest.mark.skipif(not _URI, reason="未设置 T2S_MILVUS_URI，跳过 Milvus 集成测试")
def test_upsert_and_search():
    store = MilvusVectorStore(uri=_URI, collection=f"test_vec_{os.getpid()}", dim=4)
    store.upsert("a", [1.0, 0.0, 0.0, 0.0], "营业部成交额")
    store.upsert("b", [0.0, 1.0, 0.0, 0.0], "基金净值")
    hits = store.search([1.0, 0.1, 0.0, 0.0], top_k=2)
    assert hits[0]["id"] == "a" and "成交额" in hits[0]["text"]
    store.delete("a")
    store.delete("b")


def test_unavailable_raises_when_no_service():
    """未起 Milvus 服务时构造即抛降级信号（上层捕获后回落 SQLite 自建向量）。"""
    with pytest.raises(MilvusUnavailableError):
        MilvusVectorStore(uri="http://127.0.0.1:19540", collection="offline_test", dim=4)
