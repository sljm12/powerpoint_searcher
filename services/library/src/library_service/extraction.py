from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID

import httpx
from pydantic import ValidationError

from .models import Asset, Deletion, ErrorDetail, Job, Manifest


class UpstreamError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        self.detail = ErrorDetail(code=code, message=message, retryable=retryable)


class Extraction:
    def __init__(self, url: str, token: str, timeout: float = 15,
                 manifest_limit: int = 8 * 1024 * 1024,
                 asset_limit: int = 64 * 1024 * 1024,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"),
                                        headers={"Authorization": f"Bearer {token}"},
                                        timeout=httpx.Timeout(timeout), follow_redirects=False,
                                        transport=transport, trust_env=False)
        self.manifest_limit, self.asset_limit = manifest_limit, asset_limit
        self.timeout = timeout

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _status(status: int) -> None:
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise UpstreamError("extraction_authentication", "Extraction authentication failed. Check the configured service token.")
        if status == 429:
            raise UpstreamError("extraction_busy", "Extraction queue is full. This operation is waiting for capacity.", True)
        if status in (502, 503, 504):
            raise UpstreamError("extraction_unavailable", "Extraction is unavailable or busy. Check its readiness and retry.", True)
        if status in (400, 413, 415, 422):
            raise UpstreamError("presentation_rejected", "Extraction rejected the presentation. Check its format and configured limits.")
        if status in (404, 409, 410):
            raise UpstreamError("extraction_conflict", "Extraction data changed or is unavailable. Refresh status or reprocess the deck.")
        raise UpstreamError("extraction_unavailable", "Extraction returned an unexpected response. Check the service and retry.", status >= 500)

    async def _read(self, response: httpx.Response, limit: int) -> bytes:
        length = response.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise UpstreamError("invalid_extraction_response", "Extraction response exceeds the supported size.")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > limit:
                raise UpstreamError("invalid_extraction_response", "Extraction response exceeds the supported size.")
            data.extend(chunk)
        return bytes(data)

    async def _json(self, method: str, path: str,
                    data: dict[str, str] | None = None,
                    files: dict[str, tuple[str, BinaryIO, str]] | None = None,
                    headers: dict[str, str] | None = None) -> object:
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream(method, path, data=data, files=files, headers=headers) as response:
                    self._status(response.status_code)
                    if response.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                        raise UpstreamError("invalid_extraction_response", "Extraction returned an invalid JSON response.")
                    body = await self._read(response, self.manifest_limit)
                    return cast(object, json.loads(body))
        except (httpx.HTTPError, TimeoutError):
            raise UpstreamError("extraction_unavailable", "Cannot reach extraction. Check its URL, readiness, and service token, then retry.", True) from None
        except (ValueError, json.JSONDecodeError):
            raise UpstreamError("invalid_extraction_response", "Extraction returned malformed JSON.") from None

    async def submit(self, deck_id: UUID, revision: int, key: str, source: Path, filename: str) -> Job:
        with source.open("rb") as handle:
            value = await self._json("POST", f"/v1/decks/{deck_id}/extractions",
                                     data={"source_revision": str(revision), "idempotency_key": key},
                                     files={"file": (filename, handle, "application/vnd.openxmlformats-officedocument.presentationml.presentation")})
        return self._job(value)

    @staticmethod
    def _job(value: object) -> Job:
        try:
            return Job.model_validate(value)
        except ValidationError:
            raise UpstreamError("invalid_extraction_response", "Extraction job failed response validation.") from None

    async def job(self, identifier: UUID) -> Job:
        return self._job(await self._json("GET", f"/v1/jobs/{identifier}"))

    async def retry(self, identifier: UUID, key: str) -> Job:
        return self._job(await self._json("POST", f"/v1/jobs/{identifier}/retry",
                                         headers={"Idempotency-Key": key}))

    async def manifest(self, deck_id: UUID, generation: UUID) -> Manifest:
        value = await self._json("GET", f"/v1/decks/{deck_id}/manifest?generation={generation}")
        try:
            return Manifest.model_validate(value)
        except ValidationError:
            raise UpstreamError("invalid_extraction_response", "Extraction manifest failed response validation.") from None

    async def delete(self, deck_id: UUID) -> Deletion:
        value = await self._json("DELETE", f"/v1/decks/{deck_id}")
        try:
            result = Deletion.model_validate(value)
        except ValidationError:
            raise UpstreamError("invalid_extraction_response", "Extraction cleanup failed response validation.") from None
        if result.deck_id != deck_id:
            raise UpstreamError("invalid_extraction_response", "Extraction cleanup returned the wrong deck identity.")
        return result

    async def asset(self, asset: Asset) -> bytes:
        if asset.byte_length > self.asset_limit:
            raise UpstreamError("asset_limit", "The graphic exceeds the configured download size.")
        try:
            async with asyncio.timeout(self.timeout):
                async with self.client.stream("GET", f"/v1/assets/{asset.asset_id}") as response:
                    self._status(response.status_code)
                    data = await self._read(response, min(asset.byte_length, self.asset_limit))
                    if (response.headers.get("content-type", "").split(";", 1)[0] != asset.media_type
                            or response.headers.get("etag") != f'"{asset.sha256}"'
                            or len(data) != asset.byte_length
                            or hashlib.sha256(data).hexdigest() != asset.sha256):
                        raise UpstreamError("invalid_extraction_response", "Graphic download failed its integrity check.")
                    return data
        except (httpx.HTTPError, TimeoutError):
            raise UpstreamError("extraction_unavailable", "Graphic download could not reach extraction. Retry after the service recovers.", True) from None
