from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from typing import Any

import httpx

from .settings import Settings


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class Embeddings:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        headers = {"Authorization": f"Bearer {settings.embedding_api_key}"} if settings.embedding_api_key else {}
        self.client = httpx.AsyncClient(
            base_url=settings.embedding_url.rstrip("/") + "/",
            headers=headers,
            timeout=httpx.Timeout(settings.embedding_timeout),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def embed(self, inputs: Sequence[str], *, retry_indexing: bool = False) -> list[list[float]]:
        if not inputs or any(not isinstance(item, str) or not item for item in inputs):
            raise EmbeddingError("invalid_embedding_input", False)
        attempts = self.settings.max_attempts if retry_indexing else 1
        for attempt in range(attempts):
            try:
                return await self._request(inputs)
            except EmbeddingError as error:
                if not error.retryable or attempt + 1 == attempts:
                    raise
                await asyncio.sleep(min(2 ** attempt, 4))
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 == attempts:
                    raise EmbeddingError("embedding_unavailable", True) from None
                await asyncio.sleep(min(2 ** attempt, 4))
        raise EmbeddingError("embedding_unavailable", True)

    async def _request(self, inputs: Sequence[str]) -> list[list[float]]:
        body: dict[str, Any] = {
            "model": self.settings.embedding_model,
            "input": list(inputs),
            "encoding_format": "float",
        }
        if self.settings.embedding_request_dimensions is not None:
            body["dimensions"] = self.settings.embedding_request_dimensions
        try:
            async with self.client.stream("POST", "embeddings", json=body) as response:
                if response.status_code in (408, 429) or response.status_code >= 500:
                    raise EmbeddingError("embedding_unavailable", True)
                if response.status_code != 200:
                    raise EmbeddingError("embedding_configuration_error", False)
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.settings.max_response_bytes:
                        raise EmbeddingError("embedding_response_too_large", False)
                    chunks.append(chunk)
        except httpx.TimeoutException:
            raise EmbeddingError("embedding_unavailable", True) from None
        except httpx.NetworkError:
            raise EmbeddingError("embedding_unavailable", True) from None
        try:
            payload = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise EmbeddingError("invalid_embedding_response", False) from None
        if not isinstance(payload, dict) or payload.get("model") != (
            self.settings.embedding_expected_model or self.settings.embedding_model
        ):
            raise EmbeddingError("unexpected_embedding_model", False)
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(inputs):
            raise EmbeddingError("invalid_embedding_response", False)
        vectors: list[list[float] | None] = [None] * len(inputs)
        for item in data:
            if not isinstance(item, dict) or type(item.get("index")) is not int:
                raise EmbeddingError("invalid_embedding_response", False)
            index = item["index"]
            values = item.get("embedding")
            if not 0 <= index < len(inputs) or vectors[index] is not None or not isinstance(values, list):
                raise EmbeddingError("invalid_embedding_response", False)
            if len(values) != self.settings.embedding_dimensions or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                for value in values
            ):
                raise EmbeddingError("invalid_embedding_vector", False)
            norm = math.sqrt(sum(float(value) ** 2 for value in values))
            if not math.isfinite(norm) or norm == 0:
                raise EmbeddingError("invalid_embedding_vector", False)
            vectors[index] = [float(value) / norm for value in values]
        if any(vector is None for vector in vectors):
            raise EmbeddingError("invalid_embedding_response", False)
        return [vector for vector in vectors if vector is not None]
