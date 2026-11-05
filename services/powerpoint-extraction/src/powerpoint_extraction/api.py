from __future__ import annotations

import asyncio
import hmac
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .models import (
    Deletion,
    ErrorResponse,
    ExtractionResult,
    Extractor,
    Health,
    Job,
    Manifest,
    Progress,
    ServiceError,
)
from .store import Store
from .worker import Worker


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str
    upload_limit: int = 64 * 1024 * 1024
    request_limit: int = 65 * 1024 * 1024
    manifest_limit: int = 8 * 1024 * 1024
    queue_limit: int = 8
    conversion_timeout: float = 180
    body_timeout: float = 30

    def __post_init__(self) -> None:
        if len(self.token.encode()) < 32:
            raise ValueError("EX1_TOKEN must contain at least 32 bytes")
        if min(self.upload_limit, self.request_limit, self.manifest_limit, self.queue_limit,
               self.conversion_timeout, self.body_timeout) <= 0 or self.upload_limit > self.request_limit:
            raise ValueError("Invalid service limits")

    @classmethod
    def from_environment(cls) -> Settings:
        root = os.environ.get("EX1_DATA_DIR")
        if not root:
            raise ValueError("EX1_DATA_DIR must name a private local data directory")
        return cls(data_dir=Path(root), token=os.environ.get("EX1_TOKEN", ""))


def private_directory(root: Path) -> None:
    if root.is_symlink() or root.is_junction() or any(path.is_symlink() or path.is_junction() for path in root.parents):
        raise ValueError("Private extraction directory cannot use symbolic links")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(path.is_symlink() or path.is_junction() for path in root.rglob("*")):
        raise ValueError("Private extraction directory contains a symbolic link")
    if sys.platform == "win32":
        import ctypes
        filesystem = ctypes.create_unicode_buffer(32)
        volume = Path(root.resolve().anchor)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(str(volume), None, 0, None, None, None, filesystem, 32)
        if not ok or filesystem.value != "NTFS":
            raise ValueError("Private extraction data requires an NTFS volume on Windows")
        script = "$ErrorActionPreference='Stop'; $p=$env:EX1_PRIVATE_DATA_DIR; $a=Get-Acl -LiteralPath $p; $a.SetAccessRuleProtection($true,$false); foreach($r in @($a.Access)){$a.RemoveAccessRuleSpecific($r)}; $s=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; $r=New-Object System.Security.AccessControl.FileSystemAccessRule($s,'FullControl','ContainerInherit,ObjectInherit','None','Allow'); $a.AddAccessRule($r); [System.IO.Directory]::SetAccessControl($p,$a)"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       env={**os.environ, "EX1_PRIVATE_DATA_DIR": str(root.resolve())},
                       check=True, timeout=15, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        root.chmod(0o700)
        if root.stat().st_uid != os.getuid():
            raise ValueError("Extraction directory must be owned by the service user")


def error_response(error: ServiceError, request_id: UUID) -> JSONResponse:
    payload = ErrorResponse(error=error.detail, request_id=request_id)
    return JSONResponse(payload.model_dump(mode="json"), status_code=error.status,
                        headers={"Cache-Control": "no-store", "X-Request-ID": str(request_id), "X-Content-Type-Options": "nosniff"})


class Boundary:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4()
        scope.setdefault("state", {})["request_id"] = request_id
        headers = dict(scope["headers"])
        if scope["path"] != "/health":
            expected = b"Bearer " + self.settings.token.encode()
            if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                await error_response(ServiceError(401, "unauthorized", "A valid service bearer token is required."), request_id)(scope, receive, send)
                return
        try:
            length = headers.get(b"content-length")
            if length is not None and (not length.isdigit() or len(length) > 16 or int(length) > self.settings.request_limit):
                raise ServiceError(413 if length.isdigit() else 400, "request_limit" if length.isdigit() else "malformed_request", "Request size is invalid or exceeds the configured limit.")
            body = bytearray()
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
            await error_response(ServiceError(400, "upload_timeout", "Request body timed out; retry the upload.", True), request_id)(scope, receive, send)
            return
        except ServiceError as error:
            await error_response(error, request_id)(scope, receive, send)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                payload = bytes(body)
                body.clear()
                return {"type": "http.request", "body": payload, "more_body": False}
            return await receive()

        started = False

        async def response_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"] += [(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"x-request-id", str(request_id).encode())]
            await send(message)

        try:
            await self.app(scope, replay, response_headers)
        except Exception:
            if not started:
                await error_response(ServiceError(503, "service_unavailable", "Service could not complete this request; retry later.", True), request_id)(scope, receive, send)


async def unavailable_extractor(source: Path, output: Path, progress: Progress) -> ExtractionResult:
    raise RuntimeError("Renderer is not configured")


def unavailable_validator(path: Path) -> None:
    raise ServiceError(503, "renderer_unavailable", "Source validation and confined rendering must be configured.", True)


def create_app(settings: Settings, *, extractor: Extractor = unavailable_extractor,
               ready: Callable[[], bool] = lambda: False,
               validate_upload: Callable[[Path], None] = unavailable_validator,
               secure_storage: bool = True,
               initialize_renderer: Callable[[], Awaitable[None]] | None = None) -> FastAPI:
    def can_render() -> bool:
        if extractor is unavailable_extractor or validate_upload is unavailable_validator:
            return False
        try:
            return ready()
        except Exception:
            return False

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if secure_storage:
            private_directory(settings.data_dir)
        if initialize_renderer is not None:
            await initialize_renderer()
        store = Store(settings.data_dir, settings.queue_limit, settings.manifest_limit)
        incoming = store.root / "incoming"
        incoming.mkdir(exist_ok=True)
        for path in incoming.iterdir():
            path.unlink()
        worker = Worker(store, extractor, can_render, settings.conversion_timeout)
        app.state.store, app.state.worker = store, worker
        task = asyncio.create_task(worker.run())
        try:
            yield
        finally:
            await worker.stop()
            await task
            store.close()

    app = FastAPI(title="PowerPoint extraction", version="1", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None,
                  responses={status: {"model": ErrorResponse} for status in
                             (400, 401, 404, 409, 410, 413, 415, 422, 429, 503)})
    app.add_middleware(Boundary, settings=settings)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError) -> JSONResponse:
        return error_response(error, request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(ServiceError(400, "malformed_request", "Request contains invalid identifiers or fields."), request.state.request_id)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return error_response(ServiceError(error.status_code, "malformed_request" if error.status_code == 400 else "not_found", "Request or resource is unavailable."), request.state.request_id)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        return error_response(ServiceError(503, "service_unavailable", "Service could not complete this request; retry later.", True), request.state.request_id)

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health(ready=can_render())

    def require_ready() -> None:
        if not can_render():
            raise ServiceError(503, "renderer_unavailable", "Confined renderer is unavailable; verify local renderer configuration.", True)

    @app.post("/v1/decks/{deck_id}/extractions", response_model=Job, status_code=202)
    async def submit(deck_id: UUID, request: Request) -> Job:
        require_ready()
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/form-data;"):
            raise ServiceError(415, "unsupported_type", "Upload multipart form data containing one .pptx file.")
        async with request.form(max_files=1, max_fields=2, max_part_size=512) as form:
            if set(form) != {"file", "source_revision", "idempotency_key"} or len(form.multi_items()) != 3:
                raise ServiceError(400, "malformed_request", "Expected file, source_revision and idempotency_key fields exactly once.")
            upload, revision, key = form["file"], form["source_revision"], form["idempotency_key"]
            if not isinstance(upload, UploadFile) or not isinstance(revision, str) or not isinstance(key, str):
                raise ServiceError(400, "malformed_request", "Upload fields have invalid types.")
            if not re.fullmatch(r"[1-9][0-9]{0,9}", revision) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
                raise ServiceError(400, "malformed_request", "Source revision or idempotency key is invalid.")
            if not upload.filename or not upload.filename.lower().endswith(".pptx"):
                raise ServiceError(415, "unsupported_type", "Only .pptx presentations are supported.")
            store: Store = request.app.state.store
            handle, filename = tempfile.mkstemp(prefix="upload-", suffix=".pptx", dir=store.root / "incoming")
            path = Path(filename)
            try:
                total = 0
                with os.fdopen(handle, "wb") as target:
                    while chunk := await upload.read(65536):
                        total += len(chunk)
                        if total > settings.upload_limit:
                            raise ServiceError(413, "upload_limit", "Presentation exceeds the configured byte limit.")
                        target.write(chunk)
                if not total:
                    raise ServiceError(422, "invalid_presentation", "Presentation file is empty.")
                try:
                    validate_upload(path)
                except ServiceError:
                    raise
                except ValueError:
                    raise ServiceError(422, "invalid_presentation", "Presentation is invalid, unsafe or exceeds supported content limits.") from None
                filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
                job = store.submit(deck_id, int(revision), key, path, filename)
                request.app.state.worker.wake.set()
                return job
            finally:
                path.unlink(missing_ok=True)

    @app.get("/v1/jobs/{job_id}", response_model=Job)
    async def job(job_id: UUID, request: Request) -> Job:
        store: Store = request.app.state.store
        return store.get_job(job_id)

    @app.post("/v1/jobs/{job_id}/retry", response_model=Job, status_code=202)
    async def retry(job_id: UUID, request: Request) -> Job:
        require_ready()
        store: Store = request.app.state.store
        result = store.retry(job_id)
        request.app.state.worker.wake.set()
        return result

    @app.get("/v1/decks/{deck_id}/manifest", response_model=Manifest)
    async def manifest(deck_id: UUID, request: Request, generation: Annotated[UUID | None, Query()] = None) -> Manifest:
        store: Store = request.app.state.store
        try:
            return store.manifest(deck_id, generation)
        except ValidationError:
            raise ServiceError(503, "manifest_unavailable", "Stored generation failed validation.", True) from None

    @app.get("/v1/assets/{asset_id}", response_class=Response)
    async def asset(asset_id: UUID, request: Request) -> Response:
        store: Store = request.app.state.store
        data, record = store.asset_bytes(asset_id)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
                     "image/tiff": ".tiff", "image/bmp": ".bmp", "image/svg+xml": ".svg"}.get(record.media_type, ".bin")
        return Response(data, media_type=record.media_type,
                        headers={"ETag": f'"{record.sha256}"', "Content-Length": str(record.byte_length),
                                 "Content-Disposition": f'attachment; filename="asset-{asset_id}{extension}"'})

    @app.delete("/v1/decks/{deck_id}", response_model=Deletion, status_code=202)
    async def delete(deck_id: UUID, request: Request) -> Deletion:
        store: Store = request.app.state.store
        result = store.delete(deck_id)
        request.app.state.worker.wake.set()
        return result

    @app.get("/v1/decks/{deck_id}/deletion", response_model=Deletion)
    async def deletion(deck_id: UUID, request: Request) -> Deletion:
        store: Store = request.app.state.store
        return store.deletion(deck_id)

    return app
