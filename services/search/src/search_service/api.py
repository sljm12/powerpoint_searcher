from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

import httpx
from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from tokenizers import Tokenizer

from .embeddings import Embeddings
from .models import DeleteResponse, IndexRequest, JobAccepted, JobStatus, Record, SearchRequest, SearchResponse
from .service import Search
from .settings import Settings
from .store import SearchError, SearchStore


class Boundary:
    def __init__(self, app: ASGIApp, token: str, max_body_bytes: int) -> None:
        self.app = app
        self.token = token
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope["path"] == "/health" and scope["method"] == "GET"):
            await self.app(scope, receive, send)
            return
        if not self.token:
            await self.error(send, 503, "search_unconfigured", "Search authorization is not configured.")
            return
        authorization = [value for name, value in scope["headers"] if name.lower() == b"authorization"]
        if len(authorization) != 1 or not authorization[0].startswith(b"Bearer "):
            await self.error(send, 401, "unauthorized", "A valid bearer token is required.")
            return
        token = authorization[0][len(b"Bearer "):].decode("utf-8", errors="ignore")
        if not hmac.compare_digest(token, self.token):
            await self.error(send, 401, "unauthorized", "A valid bearer token is required.")
            return
        lengths = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
        if len(lengths) > 1:
            await self.error(send, 400, "invalid_request", "Request fields are invalid.")
            return
        if lengths:
            try:
                if int(lengths[0]) > self.max_body_bytes:
                    await self.error(send, 413, "request_too_large", "Request body exceeds the supported size.")
                    return
            except ValueError:
                await self.error(send, 400, "invalid_request", "Request fields are invalid.")
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_body_bytes:
                await self.error(send, 413, "request_too_large", "Request body exceeds the supported size.")
                return
            if not message.get("more_body", False):
                break
        sent = False

        async def replay() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)

    @staticmethod
    async def error(send: Send, status: int, code: str, message: str) -> None:
        body = json.dumps({"error": {"code": code, "message": message, "retryable": False}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class DeleteRequest(Record):
    library_id: UUID


def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    store = SearchStore(settings)
    embeddings: Embeddings | None = None
    service: Search | None = None
    worker: asyncio.Task[None] | None = None
    unavailable = "search_unconfigured"

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal embeddings, service, worker, unavailable
        try:
            store.open()
        except Exception:
            unavailable = "database_unavailable"
        if settings.embedding_url and settings.embedding_model and settings.embedding_revision and settings.tokenizer_path.is_file():
            try:
                tokenizer = Tokenizer.from_file(str(settings.tokenizer_path))
                embeddings = Embeddings(settings, transport)
                service = Search(settings, store, embeddings, tokenizer)
                if unavailable != "database_unavailable":
                    await asyncio.to_thread(store.register_profile, service.profile_id,
                                            service.profile_configuration)
                    unavailable = ""
                    worker = asyncio.create_task(service.run())
            except Exception:
                unavailable = "embedding_configuration_error"
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if embeddings:
                await embeddings.close()
            store.close()

    app = FastAPI(title="PowerPoint search", docs_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(Boundary, token=settings.token, max_body_bytes=8 * 1024 * 1024)

    @app.exception_handler(SearchError)
    async def search_error(_: Request, error: SearchError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code,
            "message": error.message, "retryable": error.retryable}})

    @app.exception_handler(RequestValidationError)
    async def request_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": {"code": "invalid_request",
            "message": "Request fields are invalid.", "retryable": False}})

    def require_token(authorization: str | None) -> None:
        if not settings.token:
            raise SearchError(503, "search_unconfigured", "Search authorization is not configured.")
        token = authorization.removeprefix("Bearer ") if authorization else ""
        if not authorization or not authorization.startswith("Bearer ") or not hmac.compare_digest(token, settings.token):
            raise SearchError(401, "unauthorized", "A valid bearer token is required.")

    def ready() -> Search:
        if unavailable:
            raise SearchError(503, unavailable, "Search service is not ready. Check its local configuration.")
        if service is None:
            raise SearchError(503, "search_unconfigured", "Search model is not configured.")
        return service

    @app.get("/health")
    async def health() -> dict[str, Any]:
        db_ready, db_error = await asyncio.to_thread(store.health)
        configuration_ready = bool(service and settings.token)
        return {"live": True, "ready": db_ready and configuration_ready and not unavailable,
                "error": None if db_ready and configuration_ready and not unavailable
                else (unavailable or db_error or "search_unconfigured")}

    @app.get("/v1/policy")
    async def policy(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_token(authorization)
        search = ready()
        return {"library_id": await asyncio.to_thread(store.library_id),
                "profile_id": search.profile_id, "model": settings.embedding_model,
                "location": settings.embedding_location,
                "disclosure": "Native slide text, included speaker notes, and query text are sent to the configured embedding service. Images and original decks are not sent."}

    @app.post("/v1/index-jobs", response_model=JobAccepted, status_code=202)
    async def submit(request: IndexRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                     authorization: str | None = Header(default=None)) -> JobAccepted:
        require_token(authorization)
        if not idempotency_key or len(idempotency_key) > 200:
            raise SearchError(400, "invalid_idempotency_key", "A valid Idempotency-Key is required.")
        search = ready()
        return await search.submit(idempotency_key, request, request.model_dump(mode="json"))

    @app.get("/v1/index-jobs/{job_id}", response_model=JobStatus)
    async def job(job_id: UUID, authorization: str | None = Header(default=None)) -> JobStatus:
        require_token(authorization)
        ready()
        return await asyncio.to_thread(store.status, job_id)

    @app.post("/v1/index-jobs/{job_id}/retry", response_model=JobAccepted, status_code=202)
    async def retry(job_id: UUID, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                    authorization: str | None = Header(default=None)) -> JobAccepted:
        require_token(authorization)
        search = ready()
        if not idempotency_key:
            raise SearchError(400, "invalid_idempotency_key", "A valid Idempotency-Key is required.")
        accepted = await asyncio.to_thread(store.retry, job_id, idempotency_key)
        search.wake.set()
        return accepted

    @app.post("/v1/search", response_model=SearchResponse)
    async def search_route(request: SearchRequest, authorization: str | None = Header(default=None)) -> SearchResponse:
        require_token(authorization)
        return await ready().query(request)

    @app.delete("/v1/decks/{deck_id}", response_model=DeleteResponse)
    async def delete(deck_id: UUID, library_id: UUID = Query(), authorization: str | None = Header(default=None)) -> DeleteResponse:
        require_token(authorization)
        if store.db is None:
            raise SearchError(503, "storage_unavailable", "Search database is not available.")
        await asyncio.to_thread(store.delete, library_id, deck_id)
        return DeleteResponse(deck_id=deck_id, state="complete")

    return app
