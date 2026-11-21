from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from .settings import Settings


class EmbeddingError(RuntimeError):
    def __init__(self, code: str, retryable: bool, retry_after: float | None = None) -> None:
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        super().__init__(code)


def retry_delay(header: str | None) -> float | None:
    if not header:
        return None
    try:
        seconds = float(header)
    except ValueError:
        try:
            date = parsedate_to_datetime(header)
        except (ValueError, OverflowError):
            return None
        if date.tzinfo is None:
            return None
        seconds = (date - datetime.now(UTC)).total_seconds()
    return min(4, max(0, seconds)) if math.isfinite(seconds) else None


class Embeddings:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.embedding_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.embedding_api_key or 'local-no-key'}"},
            timeout=settings.embedding_timeout, follow_redirects=False, trust_env=False, transport=transport,
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
                await asyncio.sleep(error.retry_after if error.retry_after is not None else min(2 ** attempt, 4))
        raise EmbeddingError("embedding_unavailable", True)

    async def _request(self, inputs: Sequence[str]) -> list[list[float]]:
        payload: dict[str, object] = {"model": self.settings.embedding_model, "input": list(inputs),
                                      "encoding_format": "float"}
        if self.settings.embedding_request_dimensions is not None:
            payload["dimensions"] = self.settings.embedding_request_dimensions
        try:
            async with (asyncio.timeout(self.settings.embedding_timeout),
                        self.client.stream("POST", "embeddings", json=payload) as response):
                if not response.is_success:
                    retryable = response.status_code in (408, 429) or response.status_code >= 500
                    code = "embedding_unavailable" if retryable else "embedding_configuration_error"
                    raise EmbeddingError(code, retryable, retry_delay(response.headers.get("retry-after")))
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > self.settings.max_response_bytes:
                        raise EmbeddingError("invalid_embedding_response", False)
                    body.extend(chunk)
        except (httpx.RequestError, TimeoutError):
            raise EmbeddingError("embedding_unavailable", True) from None
        try:
            record: object = json.loads(body)
        except (ValueError, RecursionError):
            raise EmbeddingError("invalid_embedding_response", False) from None
        if not isinstance(record, dict) or not isinstance(record.get("data"), list):
            raise EmbeddingError("invalid_embedding_response", False)
        if record.get("model") != (
            self.settings.embedding_expected_model or self.settings.embedding_model
        ):
            raise EmbeddingError("unexpected_embedding_model", False)
        data = record["data"]
        if len(data) != len(inputs):
            raise EmbeddingError("invalid_embedding_response", False)
        vectors: list[list[float] | None] = [None] * len(inputs)
        for item in data:
            if not isinstance(item, dict):
                raise EmbeddingError("invalid_embedding_response", False)
            index = item.get("index")
            values = item.get("embedding")
            if type(index) is not int or not 0 <= index < len(inputs) or vectors[index] is not None:
                raise EmbeddingError("invalid_embedding_response", False)
            if not isinstance(values, list) or len(values) != self.settings.embedding_dimensions or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in values
            ):
                raise EmbeddingError("invalid_embedding_vector", False)
            try:
                norm = math.hypot(*values)
            except OverflowError:
                raise EmbeddingError("invalid_embedding_vector", False) from None
            if not math.isfinite(norm) or norm == 0:
                raise EmbeddingError("invalid_embedding_vector", False)
            vectors[index] = [float(value) / norm for value in values]
        if any(vector is None for vector in vectors):
            raise EmbeddingError("invalid_embedding_response", False)
        return [vector for vector in vectors if vector is not None]
