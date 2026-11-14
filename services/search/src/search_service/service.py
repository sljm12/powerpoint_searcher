from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from tokenizers import Tokenizer

from .domain import build_segments, profile_id
from .embeddings import EmbeddingError, Embeddings
from .models import IndexRequest, JobAccepted, SearchRequest, SearchResponse, WarningDetail
from .settings import Settings
from .store import SearchError, SearchStore


class Search:
    def __init__(self, settings: Settings, store: SearchStore, embeddings: Embeddings,
                 tokenizer: Tokenizer) -> None:
        self.settings = settings
        self.store = store
        self.embeddings = embeddings
        self.tokenizer = tokenizer
        tokenizer_digest = hashlib.sha256(settings.tokenizer_path.read_bytes()).hexdigest()
        self.profile_configuration: dict[str, object] = {
            "endpoint": settings.embedding_url,
            "model": settings.embedding_model,
            "expected_model": settings.embedding_expected_model or settings.embedding_model,
            "revision": settings.embedding_revision,
            "dimensions": settings.embedding_dimensions,
            "request_dimensions": settings.embedding_request_dimensions,
            "query_prefix": settings.query_prefix,
            "document_prefix": settings.document_prefix,
            "tokenizer_sha256": tokenizer_digest,
            "max_input_tokens": settings.max_input_tokens,
            "chunk_tokens": settings.chunk_tokens,
            "chunk_overlap": settings.chunk_overlap,
            "normalization": "unicode-whitespace-v1",
            "chunk_format": "token-window-v1",
        }
        self.profile_id = profile_id(json.dumps(self.profile_configuration, sort_keys=True, separators=(",", ":")))
        self.wake = asyncio.Event()

    async def submit(self, key: str, request: IndexRequest, body: dict[str, Any]) -> JobAccepted:
        if request.profile_id != self.profile_id:
            raise SearchError(409, "profile_conflict", "Index request does not match configured profile.")
        accepted = await asyncio.to_thread(self.store.submit, key, request, body)
        self.wake.set()
        return accepted

    async def process_one(self) -> bool:
        job = await asyncio.to_thread(self.store.claim)
        if job is None:
            return False
        try:
            request = IndexRequest.model_validate(job["payload"])
            if request.profile_id != self.profile_id:
                raise SearchError(409, "reindex_required", "This job belongs to a different embedding profile.")
            segments = build_segments(request.slides, self.tokenizer, self.settings.document_prefix,
                                      self.settings.chunk_tokens, self.settings.chunk_overlap,
                                      self.settings.max_input_tokens)
            await asyncio.to_thread(self.store.set_total, job, len(segments))
            existing = await asyncio.to_thread(self.store.existing_segments, job)
            pending = [segment for segment in segments
                       if (segment.slide_number, segment.channel, segment.ordinal) not in existing]
            batch: list[Any] = []
            batch_tokens = 0
            for segment in pending:
                token_count = len(self.tokenizer.encode(self.settings.document_prefix + segment.text).ids)
                if batch and (len(batch) >= self.settings.batch_size or
                              batch_tokens + token_count > self.settings.max_batch_tokens):
                    if not await self._index_batch(job, request, batch):
                        return True
                    batch = []
                    batch_tokens = 0
                    await asyncio.sleep(0)
                batch.append(segment)
                batch_tokens += token_count
            if batch and not await self._index_batch(job, request, batch):
                return True
            await asyncio.to_thread(self.store.publish, job, len(segments))
        except EmbeddingError as error:
            await asyncio.to_thread(self.store.fail, job, error.code, error.retryable)
        except (ValueError, TypeError):
            await asyncio.to_thread(self.store.fail, job, "invalid_index_input", False)
        except SearchError as error:
            await asyncio.to_thread(self.store.fail, job, error.code, error.retryable)
        except Exception:
            await asyncio.to_thread(self.store.fail, job, "indexing_failed", False)
        return True

    async def _index_batch(self, job: dict[str, Any], request: IndexRequest, batch: list[Any]) -> bool:
        inputs = [segment.text for segment in batch]
        prefixed = [self.settings.document_prefix + item for item in inputs]
        vectors = await self.embeddings.embed(prefixed, retry_indexing=True)
        rows = []
        for segment, vector in zip(batch, vectors, strict=True):
            vector_text = "[" + ",".join(format(value, ".9g") for value in vector) + "]"
            rows.append((request.library_id, request.deck_id, request.generation_id,
                         request.source_revision, request.index_revision, request.profile_id,
                         segment.slide_number, segment.channel, segment.ordinal, segment.text,
                         vector_text, [{"code": warning.code, "message": warning.message}
                                       for warning in segment.warnings]))
        return await asyncio.to_thread(self.store.save_batch, job, rows)

    async def run(self) -> None:
        while True:
            self.wake.clear()
            worked = await self.process_one()
            if not worked:
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=0.5)
                except TimeoutError:
                    pass

    async def query(self, request: SearchRequest) -> SearchResponse:
        if request.profile_id != self.profile_id:
            raise SearchError(409, "profile_conflict", "Search request does not match active profile.")
        deadline = time.monotonic() + self.settings.query_timeout
        warning = None
        vector: list[float] | None
        try:
            text = self.settings.query_prefix + request.query
            if len(self.tokenizer.encode(text).ids) > self.settings.max_input_tokens:
                raise SearchError(413, "query_too_large", "Query exceeds the configured model token limit.")
            reserve = min(0.25, self.settings.query_timeout / 2)
            embedding_timeout = min(self.settings.embedding_timeout, deadline - time.monotonic() - reserve)
            if embedding_timeout <= 0:
                raise TimeoutError
            vector = (await asyncio.wait_for(self.embeddings.embed([text], retry_indexing=False),
                                              timeout=embedding_timeout))[0]
        except (EmbeddingError, TimeoutError):
            vector = None
            warning = WarningDetail(code="semantic_unavailable",
                                     message="Semantic matching is unavailable. These results use slide text only.")
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.store.search, request, vector, warning, deadline=deadline),
                timeout=max(0, deadline - time.monotonic()),
            )
        except TimeoutError:
            raise SearchError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True) from None
