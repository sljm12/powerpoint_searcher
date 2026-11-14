from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask


@dataclass
class Session:
    csrf: str
    expires: float


class RequestTooLarge(Exception):
    pass


def _hash_secret(value: str, salt: bytes) -> bytes:
    return hashlib.scrypt(value.encode(), salt=salt, n=2**14, r=8, p=1)


def create_app() -> FastAPI:
    upstream = os.environ.get("LIBRARY_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ.get("LIBRARY_TOKEN", "")
    passphrase_hash = os.environ.get("TEAM_PASSPHRASE_HASH", "")
    salt_text = os.environ.get("TEAM_PASSPHRASE_SALT", "")
    local = os.environ.get("GATEWAY_LOCAL_MODE", "false").lower() == "true"
    allowed_hosts = {item.strip().lower() for item in os.environ.get("GATEWAY_ALLOWED_HOSTS", "").split(",") if item.strip()}
    app_origin = os.environ.get("GATEWAY_ORIGIN", "http://127.0.0.1:8080" if local else "")
    frontend = Path(os.environ.get("FRONTEND_DIST", "../../frontend/dist")).resolve()
    if len(token) < 32 or len(passphrase_hash) != 128 or len(salt_text) < 16:
        raise RuntimeError("Set LIBRARY_TOKEN, a scrypt TEAM_PASSPHRASE_HASH, and TEAM_PASSPHRASE_SALT.")
    if not local and os.environ.get("GATEWAY_HTTPS_TERMINATED") != "true":
        raise RuntimeError("Team mode requires HTTPS termination; set GATEWAY_HTTPS_TERMINATED=true.")
    if not allowed_hosts or not app_origin.startswith(("https://" if not local else "http://",)):
        raise RuntimeError("Set GATEWAY_ALLOWED_HOSTS and a valid GATEWAY_ORIGIN for the deployment mode.")
    salt = salt_text.encode()
    expected = bytes.fromhex(passphrase_hash)
    sessions: dict[str, Session] = {}
    failures: dict[str, tuple[int, float]] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.upstream = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5), follow_redirects=False)
        yield
        await app.state.upstream.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def origin_ok(request: Request) -> bool:
        if request.headers.get("host", "").lower() not in allowed_hosts:
            return False
        origin = request.headers.get("origin")
        return origin is None or origin == app_origin

    def find_session(request: Request) -> tuple[str, Session] | None:
        sid = request.cookies.get("library_session", "")
        session = sessions.get(sid)
        if session is None or session.expires <= time.time():
            sessions.pop(sid, None)
            return None
        return sid, session

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                            headers={"Cache-Control": "no-store"})

    @app.post("/auth/login")
    async def login(request: Request) -> Response:
        if not origin_ok(request):
            return error(403, "origin_denied", "Request origin is not allowed.")
        ip = request.client.host if request.client else "unknown"
        count, until = failures.get(ip, (0, 0))
        if until <= time.time():
            count, until = 0, 0
        if until > time.time():
            return error(429, "rate_limited", "Too many sign-in attempts. Try again later.")
        try:
            payload = await request.json()
            supplied = payload.get("passphrase") if isinstance(payload, dict) else None
        except Exception:
            return error(400, "invalid_request", "A passphrase is required.")
        if not isinstance(supplied, str) or len(supplied) > 1024:
            return error(400, "invalid_request", "A passphrase is required.")
        if not hmac.compare_digest(_hash_secret(supplied, salt), expected):
            failures.pop(ip, None) if len(failures) > 2048 else None
            count += 1
            failures[ip] = (0, time.time() + 60) if count >= 8 else (count, 0)
            return error(401, "unauthorized", "The passphrase is incorrect.")
        failures.pop(ip, None)
        sid = secrets.token_urlsafe(32)
        if len(sessions) >= 2048:
            sessions.pop(next(iter(sessions)))
        sessions[sid] = Session(secrets.token_urlsafe(32), time.time() + 8 * 60 * 60)
        response = JSONResponse({"authenticated": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie("library_session", sid, httponly=True, secure=not local, samesite="lax",
                            path="/", max_age=8 * 60 * 60)
        return response

    @app.get("/auth/session")
    async def session(request: Request) -> Response:
        active = find_session(request)
        if active is None:
            return error(401, "unauthorized", "Sign in to access the library.")
        return JSONResponse({"authenticated": True, "csrf_token": active[1].csrf},
                            headers={"Cache-Control": "no-store"})

    @app.post("/auth/logout")
    async def logout(request: Request) -> Response:
        if not origin_ok(request):
            return error(403, "origin_denied", "Request origin is not allowed.")
        active = find_session(request)
        if active is None or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), active[1].csrf):
            return error(403, "csrf_denied", "The session or CSRF token is invalid.")
        sessions.pop(active[0], None)
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        response.delete_cookie("library_session", path="/")
        return response

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def proxy(path: str, request: Request) -> Response:
        if not origin_ok(request):
            return error(403, "origin_denied", "Request origin is not allowed.")
        active = find_session(request)
        if active is None:
            return error(401, "unauthorized", "Sign in to access the library.")
        if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), active[1].csrf):
            return error(403, "csrf_denied", "The CSRF token is invalid.")
        content_length = request.headers.get("content-length", "0") or "0"
        if not content_length.isdecimal():
            return error(400, "invalid_content_length", "The request content length is invalid.")
        if int(content_length) > 256 * 1024 * 1024:
            return error(413, "request_too_large", "The request exceeds the gateway upload limit.")

        async def bounded_body() -> AsyncIterator[bytes]:
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > 256 * 1024 * 1024:
                    raise RequestTooLarge
                yield chunk
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in {"authorization", "host", "cookie", "content-length", "forwarded", "remote-user"}
                   and not k.lower().startswith(("x-forwarded-", "x-user-", "x-auth-", "x-remote-"))}
        headers["Authorization"] = f"Bearer {token}"
        url = f"{upstream}/v1/{quote(path, safe='/')}"
        try:
            upstream_request = app.state.upstream.build_request(
                request.method, url, params=request.url.query, headers=headers,
                content=bounded_body() if request.method not in {"GET", "HEAD"} else None)
            upstream_response = await app.state.upstream.send(upstream_request, stream=True)
        except RequestTooLarge:
            return error(413, "request_too_large", "The request exceeds the gateway upload limit.")
        except httpx.TimeoutException:
            return error(504, "upstream_timeout", "The library did not respond in time. Retry the request.")
        except httpx.HTTPError:
            return error(502, "upstream_unavailable", "The library is unavailable. Check its status and retry.")
        response_headers = {k: v for k, v in upstream_response.headers.items()
                            if k.lower() in {"content-type", "content-disposition", "content-encoding",
                                             "content-length", "x-request-id", "retry-after"}}
        response_headers["Cache-Control"] = "no-store"
        return StreamingResponse(upstream_response.aiter_raw(), status_code=upstream_response.status_code,
                                 headers=response_headers, background=BackgroundTask(upstream_response.aclose))

    @app.get("/{path:path}")
    async def static(path: str, request: Request) -> Response:
        if path.startswith(("auth/", "v1/")):
            return error(404, "not_found", "Route not found.")
        target = (frontend / path).resolve()
        if not target.is_relative_to(frontend) or not target.is_file():
            target = frontend / "index.html"
        if not target.is_file():
            return error(503, "frontend_unavailable", "Build the frontend before starting the gateway.")
        return FileResponse(target, headers={"Cache-Control": "no-store"})

    return app


app = create_app()
