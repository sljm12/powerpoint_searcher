from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from .settings import Settings


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class Embeddings:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        http_client = httpx.AsyncClient(follow_redirects=False, trust_env=False, transport=transport)
        self.client = AsyncOpenAI(
            api_key=settings.embedding_api_key or "local-no-key",
            base_url=settings.embedding_url,
            timeout=settings.embedding_timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def close(self) -> None:
        await self.client.close()

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
            except (APITimeoutError, APIConnectionError):
                if attempt + 1 == attempts:
                    raise EmbeddingError("embedding_unavailable", True) from None
                await asyncio.sleep(min(2 ** attempt, 4))
        raise EmbeddingError("embedding_unavailable", True)

    async def _request(self, inputs: Sequence[str]) -> list[list[float]]:
        try:
            response = await self.client.embeddings.create(
                model=self.settings.embedding_model,
                input=list(inputs),
                dimensions=self.settings.embedding_request_dimensions,
                encoding_format="float",
            )
        except APITimeoutError:
            raise EmbeddingError("embedding_unavailable", True) from None
        except APIConnectionError:
            raise EmbeddingError("embedding_unavailable", True) from None
        except APIStatusError as error:
            raise EmbeddingError("embedding_unavailable" if error.status_code in (408, 429) or error.status_code >= 500
                                 else "embedding_configuration_error",
                                 error.status_code in (408, 429) or error.status_code >= 500) from None
        if response.model != (
            self.settings.embedding_expected_model or self.settings.embedding_model
        ):
            raise EmbeddingError("unexpected_embedding_model", False)
        data = response.data
        if len(data) != len(inputs):
            raise EmbeddingError("invalid_embedding_response", False)
        vectors: list[list[float] | None] = [None] * len(inputs)
        for item in data:
            index = item.index
            values = item.embedding
            if not 0 <= index < len(inputs) or vectors[index] is not None:
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
