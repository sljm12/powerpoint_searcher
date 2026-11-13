from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .extraction import Extraction
from .models import (Accepted, Deck, DeletionStatus, Manifest, Operation, SearchPolicy,
                     SearchQuery, SearchResult, SearchStatus, ServiceError, UploadPolicy)
from .metadata import StorageUnavailable
from .service import Library
from .search import SearchClient
from .store import Store


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str
    extraction_url: str
    extraction_token: str
    upload_limit: int = 64 * 1024 * 1024
    request_limit: int = 65 * 1024 * 1024
    manifest_limit: int = 8 * 1024 * 1024
    asset_limit: int = 64 * 1024 * 1024
    request_timeout: float = 15
    body_timeout: float = 30
    poll_interval: float = 1
    retry_limit: int = 5
    allowed_origins: tuple[str, ...] = ()
    database_backend: Literal["sqlite", "postgresql"] = "sqlite"
    postgres_dsn: str | None = None
    include_notes: bool = True
    search_url: str | None = None
    search_token: str | None = None

    def __post_init__(self) -> None:
        if any(len(token.encode()) < 32 or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token)
               for token in (self.token, self.extraction_token)):
            raise ValueError("Service tokens require at least 32 printable ASCII bytes without spaces")
        parsed = urlsplit(self.extraction_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("EXTRACTION_URL must be an HTTP service root without credentials")
        if min(self.upload_limit, self.request_limit, self.manifest_limit, self.asset_limit,
               self.request_timeout, self.body_timeout, self.poll_interval, self.retry_limit) <= 0 or self.upload_limit > self.request_limit:
            raise ValueError("Service limits must be positive")
        if "*" in self.allowed_origins:
            raise ValueError("Configure exact frontend origins")
        if self.database_backend not in ("sqlite", "postgresql"):
            raise ValueError("LIBRARY_DB_BACKEND must be sqlite or postgresql")
        if self.database_backend == "postgresql" and not self.postgres_dsn:
            raise ValueError("Set LIBRARY_POSTGRES_DSN when LIBRARY_DB_BACKEND=postgresql")
        if self.database_backend == "sqlite" and self.postgres_dsn:
            raise ValueError("LIBRARY_POSTGRES_DSN requires LIBRARY_DB_BACKEND=postgresql")
        if bool(self.search_url) != bool(self.search_token):
            raise ValueError("Configure both LIBRARY_SEARCH_URL and LIBRARY_SEARCH_TOKEN")
        if self.search_url:
            parsed_search = urlsplit(self.search_url)
            if (parsed_search.scheme not in ("http", "https") or not parsed_search.hostname
                    or parsed_search.username or parsed_search.password or parsed_search.query
                    or parsed_search.fragment or parsed_search.path not in ("", "/")):
                raise ValueError("LIBRARY_SEARCH_URL must be a service root without credentials")
            if parsed_search.scheme == "http":
                try:
                    local = ipaddress.ip_address(parsed_search.hostname).is_loopback
                except ValueError:
                    local = parsed_search.hostname == "localhost"
                if not local:
                    raise ValueError("Non-loopback search services must use HTTPS")
            if self.search_token and (len(self.search_token.encode()) < 32 or not self.search_token.isascii()
                    or any(ord(char) < 33 or ord(char) > 126 for char in self.search_token)):
                raise ValueError("Search service tokens require at least 32 printable ASCII bytes")

    @classmethod
    def from_environment(cls) -> Settings:
        root = os.environ.get("LIBRARY_DATA_DIR")
        if not root:
            raise ValueError("Set LIBRARY_DATA_DIR to a dedicated private directory")
        backend = os.environ.get("LIBRARY_DB_BACKEND", "sqlite").strip().lower()
        raw_notes_policy = os.environ.get("LIBRARY_INCLUDE_NOTES", "true").strip().lower()
        if raw_notes_policy not in ("true", "false"):
            raise ValueError("LIBRARY_INCLUDE_NOTES must be true or false")
        return cls(data_dir=Path(root), token=os.environ.get("LIBRARY_TOKEN", ""),
                   extraction_url=os.environ.get("EXTRACTION_URL", "http://127.0.0.1:8001"),
                   extraction_token=os.environ.get("EX1_TOKEN", ""),
                   allowed_origins=tuple(origin.strip() for origin in os.environ.get("LIBRARY_ORIGINS", "").split(",") if origin.strip()),
                   database_backend=cast(Literal["sqlite", "postgresql"], backend),
                   postgres_dsn=os.environ.get("LIBRARY_POSTGRES_DSN"),
                   include_notes=raw_notes_policy == "true",
                   search_url=os.environ.get("LIBRARY_SEARCH_URL") or None,
                   search_token=os.environ.get("LIBRARY_SEARCH_TOKEN") or None)


def private_directory(root: Path) -> None:
    if root.is_symlink() or root.is_junction() or any(path.is_symlink() or path.is_junction() for path in root.parents):
        raise ValueError("Private library directory cannot use symbolic links")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(path.is_symlink() or path.is_junction() for path in root.rglob("*")):
        raise ValueError("Private library directory contains a symbolic link")
    if sys.platform == "win32":
        import ctypes
        filesystem = ctypes.create_unicode_buffer(32)
        volume = Path(root.resolve().anchor)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(str(volume), None, 0, None, None, None, filesystem, 32)
        if not ok or filesystem.value != "NTFS":
            raise ValueError("Private library data requires an NTFS volume on Windows")
        script = "$ErrorActionPreference='Stop'; $p=$env:LIBRARY_PRIVATE_DATA_DIR; $a=Get-Acl -LiteralPath $p; $a.SetAccessRuleProtection($true,$false); foreach($r in @($a.Access)){$a.RemoveAccessRuleSpecific($r)}; $s=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; $r=New-Object System.Security.AccessControl.FileSystemAccessRule($s,'FullControl','ContainerInherit,ObjectInherit','None','Allow'); $a.AddAccessRule($r); [System.IO.Directory]::SetAccessControl($p,$a)"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       env={**os.environ, "LIBRARY_PRIVATE_DATA_DIR": str(root.resolve())},
                       check=True, timeout=15, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        root.chmod(0o700)
        if root.stat().st_uid != os.getuid():
            raise ValueError("Library directory must be owned by the service user")


def error_response(error: ServiceError, request_id: UUID) -> JSONResponse:
    return JSONResponse({"error": error.detail.model_dump(mode="json"), "request_id": str(request_id)},
                        status_code=error.status)


class Boundary:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4()
        scope.setdefault("state", {})["request_id"] = request_id

        async def protected_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in (b"cache-control", b"x-content-type-options", b"x-request-id")]
                message["headers"] = headers + [(b"cache-control", b"no-store"),
                                                (b"x-content-type-options", b"nosniff"),
                                                (b"x-request-id", str(request_id).encode())]
            await send(message)

        headers = dict(scope["headers"])
        if scope["path"] != "/health" and not hmac.compare_digest(headers.get(b"authorization", b""), b"Bearer " + self.settings.token.encode()):
            await error_response(ServiceError(401, "unauthorized", "A valid library bearer token is required."), request_id)(scope, receive, protected_send)
            return
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or len(length) > 16 or int(length) > self.settings.request_limit):
            await error_response(ServiceError(413, "request_limit", "Request exceeds the configured byte limit."), request_id)(scope, receive, protected_send)
            return
        body = bytearray()
        try:
            async with asyncio.timeout(self.settings.body_timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.settings.request_limit:
                        raise ServiceError(413, "request_limit", "Request exceeds the configured byte limit.")
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await error_response(ServiceError(400, "upload_timeout", "Request body timed out. Retry the upload."), request_id)(scope, receive, protected_send)
            return
        except ServiceError as error:
            await error_response(error, request_id)(scope, receive, protected_send)
            return
        consumed = False

        async def replay() -> Message:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, protected_send)


def request_key(request: Request) -> str:
    key = request.headers.get("idempotency-key", "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
        raise ServiceError(400, "malformed_request", "Supply an Idempotency-Key with 1 to 128 ASCII letters, digits, or _ . : - characters.")
    return key


def attachment(data: bytes, filename: str, media_type: str, checksum: str) -> Response:
    return Response(data, media_type=media_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Content-Length": str(len(data)), "ETag": f'"{checksum}"'
    })


def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
               secure_storage: bool = True, start_worker: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if secure_storage:
            private_directory(settings.data_dir)
        store: Store | None = None
        extraction: Extraction | None = None
        search: SearchClient | None = None
        task: asyncio.Task[None] | None = None
        try:
            store = Store(settings.data_dir, settings.database_backend, settings.postgres_dsn,
                          settings.include_notes, settings.search_url is not None)
            extraction = Extraction(settings.extraction_url, settings.extraction_token,
                                    settings.request_timeout, settings.manifest_limit,
                                    settings.asset_limit, transport)
            if settings.search_url and settings.search_token:
                search = SearchClient(settings.search_url, settings.search_token,
                                      settings.request_timeout, transport)
            library = Library(store, extraction, settings.poll_interval, settings.retry_limit, search)
            app.state.library = library
            task = asyncio.create_task(library.run()) if start_worker else None
            app.state.worker = task
            yield
        finally:
            try:
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                if extraction is not None:
                    await extraction.close()
                if search is not None:
                    await search.close()
            finally:
                if store is not None:
                    store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Boundary, settings=settings)
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                           allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                           expose_headers=["Content-Disposition", "ETag", "X-Request-ID"])

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError) -> JSONResponse:
        return error_response(error, request.state.request_id)

    @app.exception_handler(StorageUnavailable)
    async def storage_error(request: Request, error: StorageUnavailable) -> JSONResponse:
        return error_response(ServiceError(503, "storage_unavailable",
                                           "Library storage is unavailable. Restart the service and retry the keyed request."),
                             request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(ServiceError(400, "malformed_request", "Request parameters are invalid."), request.state.request_id)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        code = "not_found" if error.status_code == 404 else "malformed_request"
        return error_response(ServiceError(error.status_code, code, "Resource is unavailable or request is invalid."), request.state.request_id)

    @app.get("/health")
    async def health(request: Request) -> dict[str, bool]:
        task = request.app.state.worker
        library: Library = request.app.state.library
        return {"ready": (task is None or not task.done()) and library.store.storage_ready()}

    @app.get("/v1/decks", response_model=list[Deck])
    async def decks(request: Request) -> list[Deck]:
        library: Library = request.app.state.library
        return library.store.decks()

    @app.get("/v1/policy", response_model=UploadPolicy)
    async def policy(request: Request) -> UploadPolicy:
        library: Library = request.app.state.library
        if library.search is None:
            search_policy = SearchPolicy(state="not_configured",
                disclosure="Text search is not configured. No text is sent to an embedding service.")
        else:
            try:
                remote = await library.search.policy()
                search_policy = SearchPolicy(state="ready", profile_id=remote.profile_id,
                    model=remote.model, location=remote.location, disclosure=remote.disclosure)
            except Exception:
                search_policy = SearchPolicy(state="unavailable",
                    disclosure="Search is temporarily unavailable. Indexing and search requests will retry when it recovers.")
        return UploadPolicy(include_notes=settings.include_notes, search=search_policy)

    @app.post("/v1/search", response_model=SearchResult)
    async def search(request: Request, query: SearchQuery) -> SearchResult:
        library: Library = request.app.state.library
        return await library.search_query(query)

    @app.post("/v1/decks/{deck_id}/search/retry", response_model=SearchStatus)
    async def retry_search(deck_id: UUID, request: Request) -> SearchStatus:
        if await request.body():
            raise ServiceError(400, "malformed_request", "This operation requires an empty body.")
        library: Library = request.app.state.library
        return library.retry_search(deck_id, request_key(request))

    @app.get("/v1/deletions", response_model=list[DeletionStatus])
    async def deletions(request: Request) -> list[DeletionStatus]:
        library: Library = request.app.state.library
        return library.store.deletions()

    @app.post("/v1/decks", response_model=Accepted, status_code=202)
    async def upload(request: Request) -> Accepted:
        key = request_key(request)
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
            raise ServiceError(415, "unsupported_type", "Upload multipart form data containing one .pptx file.")
        library: Library = request.app.state.library
        async with request.form(max_files=1, max_fields=0, max_part_size=512) as form:
            if set(form) != {"file"} or len(form.multi_items()) != 1 or not isinstance(form["file"], UploadFile):
                raise ServiceError(400, "malformed_request", "Supply exactly one file field.")
            file = form["file"]
            filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
            if not filename.lower().endswith(".pptx"):
                raise ServiceError(415, "unsupported_type", "Only .pptx presentations are supported.")
            if len(filename) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in filename):
                raise ServiceError(400, "malformed_request", "Presentation filename is invalid.")
            handle, name = tempfile.mkstemp(prefix="upload-", suffix=".pptx", dir=library.store.root / "incoming")
            source = Path(name)
            try:
                total = 0
                with os.fdopen(handle, "wb") as target:
                    while chunk := await file.read(65536):
                        total += len(chunk)
                        if total > settings.upload_limit:
                            raise ServiceError(413, "upload_limit", "Presentation exceeds the configured byte limit.")
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if not total:
                    raise ServiceError(422, "invalid_presentation", "Presentation file is empty.")
                result = library.store.upload(source, filename, key)
                library.wake.set()
                return result
            finally:
                source.unlink(missing_ok=True)

    @app.get("/v1/decks/{deck_id}", response_model=Deck)
    async def deck(deck_id: UUID, request: Request) -> Deck:
        library: Library = request.app.state.library
        return library.store.deck(deck_id)

    @app.get("/v1/operations/{operation_id}", response_model=Operation)
    async def operation(operation_id: UUID, request: Request) -> Operation:
        library: Library = request.app.state.library
        return library.store.operation(operation_id)

    async def start(deck_id: UUID, request: Request, retry: bool) -> Accepted:
        if await request.body():
            raise ServiceError(400, "malformed_request", "This operation requires an empty body.")
        library: Library = request.app.state.library
        result = library.store.start(deck_id, request_key(request), retry)
        library.wake.set()
        return result

    @app.post("/v1/decks/{deck_id}/retry", response_model=Accepted, status_code=202)
    async def retry(deck_id: UUID, request: Request) -> Accepted:
        return await start(deck_id, request, True)

    @app.post("/v1/decks/{deck_id}/reprocess", response_model=Accepted, status_code=202)
    async def reprocess(deck_id: UUID, request: Request) -> Accepted:
        return await start(deck_id, request, False)

    @app.get("/v1/decks/{deck_id}/manifest", response_model=Manifest)
    async def manifest(deck_id: UUID, request: Request,
                       generation: Annotated[UUID | None, Query()] = None) -> Manifest:
        library: Library = request.app.state.library
        return library.store.manifest(deck_id, generation)

    @app.get("/v1/decks/{deck_id}/original")
    async def original(deck_id: UUID, request: Request) -> Response:
        library: Library = request.app.state.library
        data, filename, checksum = library.store.original(deck_id)
        return attachment(data, filename, "application/vnd.openxmlformats-officedocument.presentationml.presentation", checksum)

    @app.get("/v1/decks/{deck_id}/generations/{generation_id}/assets/{asset_id}")
    async def asset(deck_id: UUID, generation_id: UUID, asset_id: UUID, request: Request) -> Response:
        library: Library = request.app.state.library
        data, filename, media_type, checksum = await library.asset(deck_id, generation_id, asset_id)
        return attachment(data, filename, media_type, checksum)

    @app.delete("/v1/decks/{deck_id}", response_model=Accepted, status_code=202)
    async def delete(deck_id: UUID, request: Request) -> Accepted:
        library: Library = request.app.state.library
        result = library.store.delete(deck_id)
        library.wake.set()
        return result

    @app.get("/v1/decks/{deck_id}/deletion", response_model=Deck)
    async def deletion(deck_id: UUID, request: Request) -> Deck:
        library: Library = request.app.state.library
        result = library.store.deck(deck_id)
        if result.state not in ("deleting", "deleted"):
            raise ServiceError(404, "not_found", "Deletion is unavailable.")
        return result

    return app
