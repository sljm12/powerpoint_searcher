from __future__ import annotations

import asyncio
import json
from typing import Literal, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import SearchResult, SearchStatus


class SearchError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.code, self.message, self.retryable = code, message, retryable


class Policy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    library_id: UUID | None
    profile_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    location: str
    disclosure: str


class Job(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: UUID
    state: str
    status_url: str


class JobStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: UUID
    library_id: UUID
    deck_id: UUID
    generation_id: UUID
    source_revision: int
    index_revision: int
    profile_id: str
    state: str
    completed_segments: int = 0
    total_segments: int = 0
    eligible_slides: int = 0
    searchable_slides: int = 0
    blank_slides: int = 0
    hidden_slides: int = 0
    warnings: list[dict[str, str]] = Field(default_factory=list)
    error: dict[str, object] | None = None


class SearchClient:
    def __init__(self, url: str, token: str, timeout: float = 15,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"),
                                        headers={"Authorization": f"Bearer {token}"},
                                        timeout=httpx.Timeout(timeout), follow_redirects=False,
                                        trust_env=False, transport=transport)
        self.timeout = timeout

    async def close(self) -> None:
        await self.client.aclose()

    async def _json(self, method: str, path: str, payload: dict[str, object] | None = None,
                    key: str | None = None) -> object:
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream(method, path, json=payload,
                        headers={"Idempotency-Key": key} if key else None) as response:
                    if not 200 <= response.status_code < 300:
                        retryable = response.status_code in (408, 429, 500, 502, 503, 504)
                        raise SearchError("search_unavailable" if retryable else "search_rejected",
                                          "Search indexing is unavailable. Check its readiness and retry.", retryable)
                    if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                        raise SearchError("invalid_search_response", "Search returned an invalid response.")
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdigit() or int(length) > 8 * 1024 * 1024):
                        raise SearchError("invalid_search_response", "Search response exceeds the supported size.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 8 * 1024 * 1024:
                            raise SearchError("invalid_search_response", "Search response exceeds the supported size.")
                        body.extend(chunk)
                    return cast(object, json.loads(body))
        except (httpx.HTTPError, TimeoutError):
            raise SearchError("search_unavailable", "Search is unavailable. Indexing will retry when it recovers.", True) from None
        except (ValueError, json.JSONDecodeError):
            raise SearchError("invalid_search_response", "Search returned malformed JSON.") from None

    @staticmethod
    def _validate(model: type[BaseModel], value: object) -> BaseModel:
        try:
            return model.model_validate(value)
        except ValidationError:
            raise SearchError("invalid_search_response", "Search returned a response that failed validation.") from None

    async def policy(self) -> Policy:
        try:
            async with asyncio.timeout(min(2, self.timeout)):
                value = await self._json("GET", "/v1/policy")
        except TimeoutError:
            raise SearchError("search_unavailable", "Search policy is temporarily unavailable.", True) from None
        return cast(Policy, self._validate(Policy, value))

    async def submit(self, payload: dict[str, object], key: str) -> Job:
        return cast(Job, self._validate(Job, await self._json("POST", "/v1/index-jobs", payload, key)))

    async def retry(self, job_id: UUID, key: str) -> Job:
        return cast(Job, self._validate(Job, await self._json(
            "POST", f"/v1/index-jobs/{job_id}/retry", None, key
        )))

    async def job(self, job_id: UUID) -> JobStatus:
        return cast(JobStatus, self._validate(JobStatus, await self._json("GET", f"/v1/index-jobs/{job_id}")))

    async def delete(self, deck_id: UUID, library_id: UUID) -> None:
        await self._json("DELETE", f"/v1/decks/{deck_id}?library_id={library_id}")

    async def query(self, payload: dict[str, object]) -> SearchResult:
        return cast(SearchResult, self._validate(SearchResult, await self._json("POST", "/v1/search", payload)))

    @staticmethod
    def public_status(job: JobStatus) -> SearchStatus:
        state: Literal["queued", "processing", "ready", "failed"] = cast(
            Literal["queued", "processing", "ready", "failed"], job.state
        ) if job.state in ("queued", "processing", "ready", "failed") else "failed"
        error = None
        if job.error:
            error = {"code": str(job.error.get("code", "indexing_failed")),
                     "message": "Search indexing failed. Check search configuration and retry.",
                     "retryable": bool(job.error.get("retryable", False))}
        from .models import ErrorDetail
        return SearchStatus(state=state, index_revision=job.index_revision, generation_id=job.generation_id,
                            profile_id=job.profile_id, job_id=job.job_id,
                            completed_segments=job.completed_segments, total_segments=job.total_segments,
                            eligible_slides=job.eligible_slides, searchable_slides=job.searchable_slides,
                            blank_slides=job.blank_slides, hidden_slides=job.hidden_slides,
                            warnings=[{key: str(value) for key, value in warning.items()} for warning in job.warnings],
                            error=ErrorDetail.model_validate(error) if error else None)
