from __future__ import annotations

import asyncio
from uuid import UUID

from .extraction import Extraction, UpstreamError
from .models import ErrorDetail, Job, ServiceError
from .store import Store, Work


class Library:
    def __init__(self, store: Store, extraction: Extraction, poll_interval: float = 1,
                 retry_limit: int = 5) -> None:
        self.store, self.extraction = store, extraction
        self.poll_interval, self.retry_limit = poll_interval, retry_limit
        self.wake = asyncio.Event()

    @staticmethod
    def _job_identity(work: Work, job: Job) -> None:
        if job.deck_id != work.deck.deck_id or job.source_revision != work.operation.source_revision:
            raise UpstreamError("invalid_extraction_response", "Extraction returned an unexpected deck or revision.")

    async def reconcile_one(self) -> bool:
        work = self.store.due()
        if work is None:
            return False
        operation = work.operation
        if operation.kind == "delete":
            local_error: ErrorDetail | None = None
            try:
                self.store.remove_original(work)
            except OSError:
                local_error = ErrorDetail(code="cleanup_failed", message="Canonical original could not be removed. Check library storage permissions.", retryable=True)
            try:
                cleanup = await self.extraction.delete(work.deck.deck_id)
                if cleanup.state == "complete" and local_error is None:
                    self.store.finish_deletion(work)
                else:
                    self.store.defer(work, local_error or ErrorDetail(code="cleanup_pending", message="Extraction cleanup is still pending.", retryable=True), self.retry_limit)
            except UpstreamError as error:
                self.store.defer(work, local_error or error.detail, self.retry_limit)
            return True
        try:
            if operation.phase == "submit":
                job = await self.extraction.submit(work.deck.deck_id, operation.source_revision,
                                                   operation.remote_key, self.store.source_path(work.deck.deck_id),
                                                   work.deck.filename or "presentation.pptx")
                self._job_identity(work, job)
                self.store.accept_job(work, job, self.poll_interval)
            elif operation.phase == "retry":
                if operation.parent_job is None:
                    raise UpstreamError("invalid_operation", "Retry operation has no extraction job. Reprocess the deck.")
                job = await self.extraction.retry(operation.parent_job, operation.remote_key)
                self._job_identity(work, job)
                self.store.accept_job(work, job, self.poll_interval)
            elif operation.phase == "poll":
                if operation.job is None:
                    raise UpstreamError("invalid_operation", "Processing operation has no extraction job. Reprocess the deck.")
                job = await self.extraction.job(operation.job.job_id)
                self._job_identity(work, job)
                if job.job_id != operation.job.job_id or job.generation_id != operation.job.generation_id:
                    raise UpstreamError("invalid_extraction_response", "Extraction changed an accepted job identity.")
                if job.state == "succeeded":
                    manifest = await self.extraction.manifest(job.deck_id, job.generation_id)
                    if (manifest.deck_id != work.deck.deck_id
                            or manifest.source_revision != operation.source_revision
                            or manifest.generation_id != job.generation_id
                            or manifest.source_sha256 != work.deck.checksum
                            or manifest.source_filename != work.deck.filename):
                        raise UpstreamError("invalid_extraction_response", "Extraction manifest does not match the canonical original and job.")
                    self.store.publish(work, manifest)
                elif job.state in ("failed", "cancelled"):
                    retryable = job.error.retryable if job.error else True
                    code = job.error.code if job.error and job.error.code in {
                        "conversion_failed", "conversion_timeout", "worker_stopped", "worker_interrupted"
                    } else "extraction_failed"
                    self.store.fail(work, ErrorDetail(code=code, message="Extraction failed. Check renderer readiness and supported presentation content, then retry.", retryable=retryable, stage=job.stage), job)
                else:
                    self.store.accept_job(work, job, self.poll_interval)
        except UpstreamError as error:
            self.store.defer(work, error.detail, self.retry_limit)
        except OSError:
            self.store.fail(work, ErrorDetail(code="original_unavailable", message="Canonical original is unavailable. Restore the library backup.", retryable=False))
        return True

    async def run(self) -> None:
        while True:
            if await self.reconcile_one():
                continue
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=min(1, self.poll_interval))
            except TimeoutError:
                pass

    async def asset(self, deck_id: UUID, generation: UUID, asset_id: UUID) -> tuple[bytes, str, str, str]:
        manifest, filename = self.store.asset(deck_id, generation, asset_id)
        asset = next(asset for asset in manifest.assets if asset.asset_id == asset_id)
        try:
            data = await self.extraction.asset(asset)
        except UpstreamError as error:
            status = 503 if error.detail.retryable else 409
            raise ServiceError(status, error.detail.code, error.detail.message, error.detail.retryable) from None
        self.store.manifest(deck_id, generation)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/tiff": ".tiff", "image/bmp": ".bmp", "image/svg+xml": ".svg"}.get(asset.media_type, ".bin")
        return data, f"{filename}-{asset.role}-{asset_id}{extension}", asset.media_type, asset.sha256
