from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from search_service.embeddings import EmbeddingError, Embeddings
from search_service.settings import Settings


def settings() -> Settings:
    return Settings(
        host="127.0.0.1", port=8002, token="test-secret", database_url="",
        database_connect_timeout=2,
        database_statement_timeout_ms=1000, query_timeout=10,
        embedding_url="http://127.0.0.1:8080/v1", embedding_api_key="",
        embedding_model="model", embedding_expected_model="model", embedding_revision="revision",
        embedding_dimensions=2, embedding_request_dimensions=None,
        query_prefix="", document_prefix="", tokenizer_path=Path("tokenizer.json"),
        max_input_tokens=100, chunk_tokens=10, chunk_overlap=2, batch_size=1,
        max_batch_tokens=10, embedding_timeout=2, max_attempts=1,
        max_response_bytes=1024, embedding_location="local", allow_cloud=False,
    )


def test_reorders_response_by_index_and_normalizes_vectors() -> None:
    async def run() -> list[list[float]]:
        def respond(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/embeddings"
            assert json.loads(request.read()) == {
                "input": ["first", "second"], "model": "model", "dimensions": None,
                "encoding_format": "float",
            }
            return httpx.Response(200, json={"model": "model", "data": [
                {"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 4]},
            ]})

        client = Embeddings(settings(), httpx.MockTransport(respond))
        try:
            return await client.embed(["first", "second"])
        finally:
            await client.close()

    assert asyncio.run(run()) == [[0.6, 0.8], [0.0, 1.0]]


@pytest.mark.parametrize("vector", [[0, 0], [float("inf"), 1]])
def test_rejects_invalid_vectors(vector: list[object]) -> None:
    async def run() -> None:
        def respond(_: httpx.Request) -> httpx.Response:
            if vector == [float("inf"), 1]:
                return httpx.Response(200, content=b'{"model":"model","data":[{"index":0,"embedding":[Infinity,1]}]}')
            return httpx.Response(200, json={"model": "model", "data": [
                {"index": 0, "embedding": vector},
            ]})

        client = Embeddings(settings(), httpx.MockTransport(respond))
        try:
            with pytest.raises(EmbeddingError) as error:
                await client.embed(["text"])
            assert error.value.code == "invalid_embedding_vector"
        finally:
            await client.close()

    asyncio.run(run())
