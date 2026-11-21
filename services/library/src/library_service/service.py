from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

from .extraction import Extraction, UpstreamError
from .models import (ErrorDetail, Job, Manifest, SearchDeckMatches, SearchQuery, SearchResult,
                     SearchStatus, ServiceError)
from .search import SearchClient, SearchError
from .store import Store, Work


class Library:
    def __init__(self, store: Store, extraction: Extraction, poll_interval: float = 1,
                 retry_limit: int = 5, search: SearchClient | None = None,
                 search_backfill: bool = False) -> None:
        self.store, self.extraction, self.search = store, extraction, search
        self.poll_interval, self.retry_limit = poll_interval, retry_limit
        self.search_backfill = search_backfill
        self.wake = asyncio.Event()

    @staticmethod
    def _job_identity(work: Work, job: Job) -> None:
        if job.deck_id != work.deck.deck_id or job.source_revision != work.operation.source_revision:
            raise UpstreamError("invalid_extraction_response", "Extraction returned an unexpected deck or revision.")
        if work.operation.include_notes is not None and job.include_notes != work.operation.include_notes:
            raise UpstreamError("invalid_extraction_response", "Extraction changed the captured speaker notes policy.")

    @staticmethod
    def _manifest_notes_policy(work: Work, manifest: Manifest) -> None:
        include_notes = work.operation.include_notes
        if include_notes is None:
            return
        for slide in manifest.content.slides:
            notes = slide.notes
            expected = {"present", "empty"} if include_notes else {"excluded"}
            if notes is None or notes.state not in expected:
                raise UpstreamError("invalid_extraction_response", "Extraction manifest does not match the captured speaker notes policy.")

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
                                                   work.deck.filename or "presentation.pptx",
                                                   operation.include_notes if operation.include_notes is not None else False)
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
                    self._manifest_notes_policy(work, manifest)
                    self.store.publish(work, manifest, self._search_payload(manifest) if self.search else None,
                                       f"search-{work.deck.deck_id}-{operation.source_revision}-{manifest.generation_id}" if self.search else None,
                                       self.search is not None)
                elif job.state in ("failed", "cancelled"):
                    retryable = job.error.retryable if job.error else True
                    code = job.error.code if job.error and job.error.code in {
                        "conversion_failed", "conversion_timeout", "worker_stopped", "worker_interrupted", "invalid_notes"
                    } else "extraction_failed"
                    message = (
                        "Speaker notes could not be read. Correct the presentation and upload it again."
                        if code == "invalid_notes"
                        else "Extraction failed. Check renderer readiness and supported presentation content, then retry."
                    )
                    self.store.fail(work, ErrorDetail(code=code, message=message,
                                                      retryable=retryable, stage=job.stage), job)
                else:
                    self.store.accept_job(work, job, self.poll_interval)
        except UpstreamError as error:
            self.store.defer(work, error.detail, self.retry_limit)
        except OSError:
            self.store.fail(work, ErrorDetail(code="original_unavailable", message="Canonical original is unavailable. Restore the library backup.", retryable=False))
        return True

    def _search_payload(self, manifest: Manifest) -> dict[str, object]:
        slides: list[dict[str, object]] = []
        for slide in manifest.content.slides:
            native: list[str] = []
            for occurrence in slide.occurrences:
                source = occurrence.source
                if source.inherited or source.source_scope != "slide":
                    continue
                if source.text_blocks:
                    text = "\n".join(block.text for block in source.text_blocks)
                elif source.paragraphs:
                    text = "\n".join(source.paragraphs)
                else:
                    text = source.text
                if text.strip():
                    native.append(text)
            native_text = "\n".join(native)
            notes = slide.notes
            notes_text = "\n".join(notes.paragraphs) if notes and notes.state == "present" else ""
            warnings = ([{"code": "notes_truncated", "message": "Speaker notes were shortened during extraction."}]
                        if notes and notes.state == "present" and (notes.truncated or len(notes_text) > 32768) else [])
            if len(native_text) > 32768:
                warnings.append({"code": "native_text_truncated", "message": "Slide text was shortened for indexing."})
            slides.append({"slide_number": slide.source.number, "hidden": slide.source.hidden,
                           "native_text": native_text[:32768], "notes_text": notes_text[:32768], "warnings": warnings})
        metadata = self.store.metadata
        assert metadata is not None
        return {"library_id": str(metadata.library_id), "deck_id": str(manifest.deck_id),
                "generation_id": str(manifest.generation_id), "source_revision": manifest.source_revision,
                "index_revision": 0, "profile_id": "pending", "slides": slides}

    def backfill_search_one(self) -> bool:
        if self.search is None or not self.search_backfill:
            return False
        manifest = self.store.next_search_backfill()
        return manifest is not None and self.store.enqueue_search_backfill(manifest, self._search_payload(manifest))

    async def reconcile_search_reindex(self) -> None:
        if self.search is None:
            return
        operation = await asyncio.to_thread(self.store.current_search_reindex)
        if operation is None:
            return
        metadata = self.store.metadata
        assert metadata is not None
        try:
            if operation.state == "queued":
                status = await self.search.reindex("begin", metadata.library_id, operation.operation_id, operation.profile_id)
                await asyncio.to_thread(self.store.update_search_reindex, operation.operation_id, status.state)
                return
            cohort = await asyncio.to_thread(self.store.search_reindex_cohort, operation, self._search_payload)
            if cohort is None:
                return
            status = await self.search.reindex("activate", metadata.library_id, operation.operation_id,
                                               operation.profile_id, cohort)
            if status.state == "complete":
                await asyncio.to_thread(self.store.complete_search_reindex, operation.operation_id, cohort,
                                        self._search_payload)
        except SearchError as error:
            await asyncio.to_thread(self.store.update_search_reindex, operation.operation_id, operation.state,
                                    ErrorDetail(code=error.code, message=error.message, retryable=error.retryable))

    async def reconcile_search_one(self) -> bool:
        if self.search is None:
            return False
        task = await asyncio.to_thread(self.store.search_work)
        if task is None:
            return False
        deck_id, payload, key, previous, retry_key, attempts = task
        try:
            metadata = self.store.metadata
            assert metadata is not None
            if previous.state == "deleting":
                await self.search.delete(deck_id, metadata.library_id)
                await asyncio.to_thread(self.store.search_deleted, deck_id, expected_status=previous,
                                         expected_key=key, expected_retry_key=retry_key)
                return True
            if payload is not None and key is not None:
                if previous.profile_id is None:
                    policy = await self.search.policy()
                    if policy.maintenance and await asyncio.to_thread(self.store.current_search_reindex) is None:
                        raise SearchError("reindex_required", "Search is paused for profile maintenance.", True)
                    pinned = previous.model_copy(update={"profile_id": policy.profile_id})
                    if not await asyncio.to_thread(self.store.search_submitted, deck_id, pinned,
                                                   expected_status=previous, expected_key=key,
                                                   expected_retry_key=retry_key):
                        return True
                    previous = pinned
                payload["profile_id"] = previous.profile_id
                job = await self.search.submit(payload, key)
                slides = payload.get("slides", [])
                slide_rows = [row for row in slides if isinstance(row, dict)] if isinstance(slides, list) else []
                hidden_count = sum(bool(row.get("hidden", False)) for row in slide_rows)
                eligible_count = len(slide_rows) - hidden_count
                searchable_count = sum(not bool(row.get("hidden", False)) and bool(
                    str(row.get("native_text", "")).strip() or str(row.get("notes_text", "")).strip()
                ) for row in slide_rows)
                job_state: Literal["queued", "processing"] = "queued" if job.state == "queued" else "processing"
                status = SearchStatus(state=job_state,
                                      index_revision=previous.index_revision,
                                      source_revision=previous.source_revision,
                                      generation_id=previous.generation_id, profile_id=previous.profile_id,
                                      job_id=job.job_id, eligible_slides=eligible_count,
                                      searchable_slides=searchable_count,
                                      blank_slides=eligible_count - searchable_count,
                                      hidden_slides=hidden_count)
                await asyncio.to_thread(self.store.search_submitted, deck_id, status,
                                         expected_status=previous, expected_key=key,
                                         expected_retry_key=retry_key, retry_after=self.poll_interval)
            elif previous.job_id is not None and retry_key:
                job = await self.search.retry(previous.job_id, retry_key)
                if job.job_id != previous.job_id:
                    raise SearchError("invalid_search_response", "Search returned an unexpected retry job.")
                retry_state = "queued" if job.state == "queued" else "processing"
                status = previous.model_copy(update={"state": retry_state, "error": None})
                await asyncio.to_thread(self.store.search_submitted, deck_id, status,
                                         expected_status=previous, expected_key=key,
                                         expected_retry_key=retry_key, retry_after=self.poll_interval,
                                         acknowledge_retry=True)
            elif previous.job_id is not None:
                remote = await self.search.job(previous.job_id)
                if (remote.deck_id != deck_id or remote.generation_id != previous.generation_id
                        or remote.index_revision != previous.index_revision or remote.job_id != previous.job_id
                        or remote.profile_id != previous.profile_id
                        or remote.source_revision != previous.source_revision
                        or remote.library_id != metadata.library_id):
                    raise SearchError("invalid_search_response", "Search returned an unexpected indexing job.")
                status = self.search.public_status(remote)
                await asyncio.to_thread(self.store.search_submitted, deck_id, status,
                                         expected_status=previous, expected_key=key,
                                         expected_retry_key=retry_key,
                                         retry_after=self.poll_interval if status.state in ("queued", "processing") else 0)
            return True
        except SearchError as error:
            deleting = previous.state == "deleting"
            delay = min(30, 2 ** min(attempts + 1, 5)) if (error.retryable or deleting) else 0
            status = previous.model_copy(update={
                "state": "deleting" if deleting else "queued" if error.retryable else "failed",
                "error": ErrorDetail(code=error.code, message=error.message, retryable=error.retryable),
            })
            await asyncio.to_thread(self.store.search_submitted, deck_id, status,
                                     expected_status=previous, expected_key=key,
                                     expected_retry_key=retry_key, retry_after=delay)
        return True

    def retry_search(self, deck_id: UUID, idempotency_key: str) -> SearchStatus:
        if self.search is None:
            raise ServiceError(503, "search_not_configured", "Text search is not configured.")
        return self.store.retry_search(deck_id, idempotency_key)

    async def search_query(self, request: SearchQuery) -> SearchResult:
        if self.search is None:
            raise ServiceError(503, "search_not_configured", "Text search is not configured.")
        try:
            if await asyncio.to_thread(self.store.current_search_reindex) is not None:
                raise ServiceError(503, "reindex_required", "Search is paused for profile maintenance.", True)
            policy = await self.search.policy()
            if policy.maintenance:
                raise ServiceError(503, "reindex_required", "Search is paused for profile maintenance.", True)
            metadata = self.store.metadata
            assert metadata is not None
            if policy.library_id is not None and policy.library_id != metadata.library_id:
                raise ServiceError(409, "search_library_conflict", "Search is bound to another library.")
            offset = request.offset
            groups: list[SearchDeckMatches] = []
            next_offset = None
            mode: str | None = None
            async with asyncio.timeout(self.search.timeout):
                while True:
                    page_start = offset
                    result = await self.search.query({"library_id": str(metadata.library_id),
                        "profile_id": policy.profile_id, "query": request.query,
                        "deck_id": str(request.deck_id) if request.deck_id else None,
                        "limit": min(100, max(20, request.limit)), "candidate_offset": offset,
                        "candidate_limit": 2000})
                    if self.store.current_search_reindex() is not None:
                        raise ServiceError(503, "reindex_required", "Search is paused for profile maintenance.", True)
                    if result.profile_id != policy.profile_id or mode not in (None, result.mode):
                        raise ServiceError(503, "search_changed", "Search changed while reading results. Try again.", True)
                    mode = result.mode
                    page = self._search_page(result, request.deck_id)
                    for rank, entry in enumerate(page, 1):
                        if entry is not None:
                            groups.append(entry)
                            if len(groups) == request.limit:
                                if rank < len(page) or result.next_offset is not None:
                                    next_offset = offset + rank
                                break
                    if (len(groups) == request.limit or result.next_offset is None
                            or result.next_offset < page_start + len(page) or result.next_offset <= page_start):
                        break
                    offset = result.next_offset
        except SearchError as error:
            raise ServiceError(503, error.code, error.message, error.retryable) from None
        except TimeoutError:
            raise ServiceError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True) from None
        if request.deck_id and groups:
            groups = [groups[0].model_copy(update={
                "matches": [match for group in groups for match in group.matches],
                "has_more_matches": next_offset is not None,
            })]
        groups = [visible for group in groups if (visible := self._search_group(group, result.profile_id)) is not None]
        coverage = {field: 0 for field in ("eligible_slides", "searchable_slides", "blank_slides", "hidden_slides")}
        for deck in self.store.decks():
            if request.deck_id is not None and deck.deck_id != request.deck_id:
                continue
            if (deck.search is not None and deck.search.state == "ready"
                    and deck.search.generation_id == deck.generation_id and deck.search.profile_id == result.profile_id):
                for field in coverage:
                    coverage[field] += getattr(deck.search, field)
        return result.model_copy(update={"decks": groups, "coverage": coverage, "next_offset": next_offset})

    def _search_page(self, result: SearchResult, deck_id: UUID | None) -> list[SearchDeckMatches | None]:
        if deck_id is None:
            return [self._search_group(group, result.profile_id) for group in result.decks]
        page: list[SearchDeckMatches | None] = []
        for group in result.decks:
            visible = self._search_group(group, result.profile_id) if group.deck_id == deck_id else None
            matches = {match.slide_number: match for match in visible.matches} if visible else {}
            for raw in group.matches:
                match = matches.get(raw.slide_number)
                page.append(visible.model_copy(update={"matches": [match]}) if visible and match else None)
        return page

    def _search_group(self, group: SearchDeckMatches, profile_id: str) -> SearchDeckMatches | None:
        try:
            deck = self.store.deck(group.deck_id)
            current = deck.search
            if (deck.generation_id is None or current is None or current.state != "ready"
                    or current.generation_id != deck.generation_id or current.profile_id != profile_id):
                return None
            manifest = self.store.manifest(group.deck_id, deck.generation_id)
            if current.source_revision != manifest.source_revision:
                return None
        except ServiceError:
            return None
        previews = {asset.local_id: asset for asset in manifest.assets}
        visible_slides = {slide.source.number: slide.preview.asset_local_id
                          for slide in manifest.content.slides if not slide.source.hidden}
        matches = []
        for match in group.matches:
            local_id = visible_slides.get(match.slide_number)
            if (match.generation_id != deck.generation_id or match.index_revision != current.index_revision
                    or local_id is None or local_id not in previews):
                continue
            asset = previews[local_id]
            matches.append(match.model_copy(update={
                "preview_url": f"/v1/decks/{group.deck_id}/generations/{manifest.generation_id}/assets/{asset.asset_id}"
            }))
        if not matches:
            return None
        return group.model_copy(update={"filename": deck.filename or "Presentation", "matches": matches})

    async def run(self) -> None:
        while True:
            await self.reconcile_search_reindex()
            if await self.reconcile_search_one():
                continue
            if await self.reconcile_one():
                continue
            if await asyncio.to_thread(self.backfill_search_one):
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
