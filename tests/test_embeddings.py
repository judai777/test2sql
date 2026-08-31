"""EmbeddingClient：协议解析与错误路径（MockTransport，零网络）。"""
from __future__ import annotations

import httpx
import pytest

from t2s.config import EmbeddingConfig
from t2s.llm import EmbeddingClient, EmbeddingError


def make_client(handler) -> EmbeddingClient:
    return EmbeddingClient(EmbeddingConfig(base_url="http://x/v1", api_key="k", model="m"),
                           transport=httpx.MockTransport(handler))


def test_parse_sorted_by_index():
    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.read())
        assert body["model"] == "m" and body["input"] == ["a", "b"]
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [3.0, 4.0]},
            {"index": 0, "embedding": [1.0, 2.0]},
        ]})

    assert make_client(handler).embed(["a", "b"]) == [[1.0, 2.0], [3.0, 4.0]]


def test_empty_input_no_request():
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"data": []})

    assert make_client(handler).embed([]) == []
    assert called["n"] == 0


def test_http_error_raises():
    with pytest.raises(EmbeddingError, match="500"):
        make_client(lambda request: httpx.Response(500, text="boom")).embed(["a"])


def test_count_mismatch_raises():
    with pytest.raises(EmbeddingError, match="数量不符"):
        make_client(lambda request: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0]}]})).embed(["a", "b"])
