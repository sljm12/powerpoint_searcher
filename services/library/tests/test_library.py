from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from library_service.api import Settings, create_app
from library_service.extraction import Extraction, UpstreamError
from library_service.models import ErrorDetail, Job, Manifest, SearchStatus, ServiceError
from library_service.service import Library
from library_service.store import Store

TOKEN = "library-token-with-at-least-32-bytes"
EXTRACTION_TOKEN = "extraction-token-with-at-least-32-bytes"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PREVIEW = b"slide PNG bytes"
ORIGINAL = b"original embedded picture bytes"
CROP = b"contextual crop PNG bytes"


def manifest(deck: UUID, generation: UUID, revision: int, checksum: str, filename: str,
             notes: dict[str, object] | None = None) -> dict[str, object]:
    def asset(local_id: str, role: str, data: bytes, media_type: str = "image/png") -> dict[str, object]:
        identifier = uuid4()
        return {"asset_id": str(identifier), "local_id": local_id, "role": role,
                "media_type": media_type, "pixel_size": [100, 50], "byte_length": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "download_url": f"/v1/assets/{identifier}"}

    warning = {"code": "contextual_crop", "message": "Slide crop may include background and surrounding objects.", "affected_occurrence_ids": ["picture-1"]}
    source = {"id": "picture-1", "slide": 1, "source_part": "ppt/slides/slide1.xml",
              "source_shape_id": 2, "source_name": "Picture 1", "renderer_name": "ps_picture",
              "parent_id": None, "kind": "picture", "inherited": False, "source_scope": "slide",
              "text": "Native text", "transform": [1, 0, 0, 1, 0, 0], "bounds": [0, 0, 100, 50],
              "source_size": [100, 50], "local_geometry": [0, 0, 100, 50], "group_path": [],
              "stacking_order": 0, "alt_text": "Picture description", "paragraphs": ["Native text"],
              "text_blocks": [{"text": "Native text", "paragraphs": ["Native text"], "source_kind": "native",
                               "cell_row": None, "cell_column": None, "reading_order": None}],
              "crop": None, "asset_id": "original", "warning": None}
    preview = {"asset_local_id": "preview", "status": "rendered", "method": "slide_render",
               "pixel_size": [100, 50], "transparent": False, "crop_rectangle": None, "warnings": []}
    crop = {"asset_local_id": "crop", "status": "rendered", "method": "slide_crop",
            "pixel_size": [100, 50], "transparent": False, "crop_rectangle": [0, 0, 100, 50], "warnings": [warning]}
    return {"schema_version": 1, "deck_id": str(deck), "source_revision": revision,
            "generation_id": str(generation), "source_sha256": checksum, "source_filename": filename,
            "canonical_original_reference": None,
            "content": {"schema_version": 1, "extractor_version": "fixture-1", "renderer_version": "fixture-1",
                        "config_fingerprint": "0" * 64,
                        "slides": [{"source": {"number": 1, "source_slide_id": 256, "hidden": False, "width": 100, "height": 50},
                                    **({"notes": notes} if notes is not None else {}),
                                    "preview": preview, "occurrences": [{"source": source, "original_asset_local_id": "original",
                                                                        "original_available": True, "primary": True, "rendition": crop}]}]},
            "assets": [asset("preview", "slide_preview", PREVIEW), asset("original", "original_picture", ORIGINAL, "image/jpeg"),
                       asset("crop", "contextual_crop", CROP)], "warnings": [warning]}


class Remote:
    def __init__(self) -> None:
        self.jobs: dict[UUID, Job] = {}
        self.keys: dict[str, Job] = {}
        self.manifests: dict[UUID, dict[str, object]] = {}
        self.filename = "team.pptx"
        self.checksum = hashlib.sha256(b"deck").hexdigest()
        self.fail_conversion = False
        self.fail_notes = False
        self.fail_delete = False
        self.fail_retry = False
        self.lose_retry_response = False
        self.corrupt_manifest = False
        self.mismatch_notes_policy = False
        self.asset_started: asyncio.Event | None = None
        self.asset_continue: asyncio.Event | None = None

    def new_job(self, deck: UUID, revision: int, attempt: int = 1,
                include_notes: bool = True, notes_text_limit: int = 4096) -> Job:
        identifier = uuid4()
        job = Job(job_id=identifier, deck_id=deck, source_revision=revision,
                  generation_id=uuid4(), include_notes=include_notes,
                  notes_text_limit=notes_text_limit,
                  state="queued", stage="queued", attempt=attempt,
                  completed_slides=0, total_slides=1, created_at="2026-11-07T00:00:00+00:00",
                  updated_at="2026-11-07T00:00:00+00:00", status_url=f"/v1/jobs/{identifier}")
        self.jobs[identifier] = job
        return job

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {EXTRACTION_TOKEN}"
        parts = request.url.path.strip("/").split("/")
        if request.method == "POST" and parts[-1] == "extractions":
            body = await request.aread()
            assert b"deck" in body and b'name="idempotency_key"' in body
            fields = body.decode("latin1").split("\r\n")
            revision = int(fields[fields.index('Content-Disposition: form-data; name="source_revision"') + 2])
            key = fields[fields.index('Content-Disposition: form-data; name="idempotency_key"') + 2]
            include_notes = fields[fields.index('Content-Disposition: form-data; name="include_notes"') + 2] == "true"
            job = self.keys.get(key) or self.new_job(UUID(parts[2]), revision,
                                                     include_notes=include_notes)
            self.keys[key] = job
            return httpx.Response(202, json=job.model_dump(mode="json"))
        if request.method == "POST" and parts[-1] == "retry":
            if self.fail_retry:
                raise httpx.ConnectError("retry unavailable", request=request)
            key = request.headers["idempotency-key"]
            parent = self.jobs[UUID(parts[2])]
            job = self.keys.get(key) or self.new_job(
                parent.deck_id, parent.source_revision, parent.attempt + 1,
                parent.include_notes, parent.notes_text_limit,
            )
            self.keys[key] = job
            if self.lose_retry_response:
                self.lose_retry_response = False
                raise httpx.ReadTimeout("lost after acceptance", request=request)
            return httpx.Response(202, json=job.model_dump(mode="json"))
        if request.method == "GET" and parts[1] == "jobs":
            job = self.jobs[UUID(parts[2])]
            if self.fail_notes:
                value = job.model_dump(mode="json")
                value.update(state="failed", stage="extracting", error={"code": "invalid_notes", "message": "safe", "retryable": False, "stage": "extracting"})
                job = Job.model_validate(value)
            elif self.fail_conversion:
                value = job.model_dump(mode="json")
                value.update(state="failed", stage="rendering", error={"code": "conversion_failed", "message": "safe", "retryable": True, "stage": "rendering"})
                job = Job.model_validate(value)
            else:
                value = job.model_dump(mode="json")
                value.update(state="succeeded", stage="publishing", completed_slides=1)
                job = Job.model_validate(value)
                notes = {"state": "present", "paragraphs": ["Speaker note"], "truncated": False} if job.include_notes else {"state": "excluded", "paragraphs": [], "truncated": False}
                if self.mismatch_notes_policy:
                    notes = {"state": "excluded", "paragraphs": [], "truncated": False} if job.include_notes else {"state": "present", "paragraphs": ["Speaker note"], "truncated": False}
                value_manifest = manifest(job.deck_id, job.generation_id, job.source_revision,
                                          self.checksum, self.filename, notes)
                if self.corrupt_manifest:
                    value_manifest["source_sha256"] = "1" * 64
                self.manifests[job.generation_id] = value_manifest
            self.jobs[job.job_id] = job
            return httpx.Response(200, json=job.model_dump(mode="json"))
        if request.method == "GET" and parts[-1] == "manifest":
            return httpx.Response(200, json=self.manifests[UUID(request.url.params["generation"])])
        if request.method == "DELETE":
            if self.fail_delete:
                raise httpx.ConnectError("private failure", request=request)
            return httpx.Response(202, json={"deck_id": parts[2], "state": "complete", "error": None})
        if request.method == "GET" and parts[1] == "assets":
            if self.asset_started and self.asset_continue:
                self.asset_started.set()
                await self.asset_continue.wait()
            for value in self.manifests.values():
                parsed = Manifest.model_validate(value)
                for item in parsed.assets:
                    if str(item.asset_id) == parts[2]:
                        data = {"preview": PREVIEW, "original": ORIGINAL, "crop": CROP}[item.local_id]
                        return httpx.Response(200, content=data, headers={"content-type": item.media_type, "etag": f'"{item.sha256}"'})
        return httpx.Response(404)


def put_source(root: Path, data: bytes = b"deck") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"{uuid4()}.pptx"
    source.write_bytes(data)
    return source


def corrupt_sqlite_deck(root: Path, deck_id: str) -> None:
    with sqlite3.connect(root / "metadata.sqlite3") as database:
        database.execute("UPDATE decks SET record=? WHERE id=?",
                         ('{"private_slide_text":"never return this"}', deck_id))


def library(root: Path, remote: Remote) -> Library:
    return Library(Store(root), Extraction("http://extraction.test", EXTRACTION_TOKEN,
                                          transport=httpx.MockTransport(remote)), poll_interval=0.01)


def test_upload_idempotency_restart_and_complete_generation(tmp_path: Path) -> None:
    async def run() -> None:
        remote = Remote()
        clock = [0.0]
        with patch("library_service.store.time.time", side_effect=lambda: clock[0]):
            service = library(tmp_path / "data", remote)
            accepted = service.store.upload(put_source(tmp_path), "team.pptx", "upload-key")
            replay = service.store.upload(put_source(tmp_path), "team.pptx", "upload-key")
            assert replay == accepted
            assert len(service.store.decks()) == 1
            with pytest.raises(ServiceError) as conflict:
                service.store.upload(put_source(tmp_path, b"different"), "team.pptx", "upload-key")
            assert conflict.value.status == 409
            assert await service.reconcile_one()
            await service.extraction.close()
            service.store.close()
            service = library(tmp_path / "data", remote)
            clock[0] += 10
            assert await service.reconcile_one()
            deck = service.store.deck(accepted.deck_id)
            assert deck.state == "extracted" and deck.generation_id is not None
            assert service.store.operation(accepted.operation_id).state == "succeeded"
            output = service.store.manifest(deck.deck_id)
            assert output.content.slides[0].notes is not None
            assert output.content.slides[0].notes.state == "present"
            assert output.content.slides[0].occurrences[0].source.text == "Native text"
            assert output.warnings[0].code == "contextual_crop"
            assert all(asset.download_url.startswith(f"/v1/decks/{deck.deck_id}/generations/") for asset in output.assets)
            original, name, checksum = service.store.original(deck.deck_id)
            assert original == b"deck" and name == "team.pptx" and checksum == remote.checksum
            data, name, media_type, checksum = await service.asset(deck.deck_id, output.generation_id, output.assets[2].asset_id)
            assert data == CROP and "slide-1-contextual_crop" in name and media_type == "image/png"
            assert checksum == hashlib.sha256(data).hexdigest()
            await service.extraction.close()
            service.store.close()
    asyncio.run(run())


def test_upload_and_deletion_survive_restart_on_each_backend(
        tmp_path: Path, database_backend: tuple[str, str | None]) -> None:
    backend, dsn = database_backend
    root = tmp_path / "data"
    source = put_source(tmp_path, b"shared backend fixture")
    store = Store(root, backend, dsn)
    accepted = store.upload(source, "team.pptx", "shared-backend-upload")
    original, filename, checksum = store.original(accepted.deck_id)
    store.delete(accepted.deck_id)
    store.close()

    reopened = Store(root, backend, dsn)
    try:
        with pytest.raises(ServiceError) as deleted:
            reopened.original(accepted.deck_id)
        assert deleted.value.status == 410
        assert any(item.deck_id == accepted.deck_id and item.state in ("deleting", "deleted")
                   for item in reopened.deletions())
        assert filename == "team.pptx" and checksum == hashlib.sha256(original).hexdigest()
    finally:
        reopened.close()


def test_retry_recovers_lost_response_without_new_revision_or_duplicate_attempt(tmp_path: Path) -> None:
    async def run() -> None:
        remote = Remote()
        remote.fail_conversion = True
        clock = [0.0]
        with patch("library_service.store.time.time", side_effect=lambda: clock[0]):
            service = library(tmp_path / "data", remote)
            accepted = service.store.upload(put_source(tmp_path), "team.pptx", "upload")
            await service.reconcile_one()
            clock[0] += 10
            await service.reconcile_one()
            assert service.store.deck(accepted.deck_id).state == "failed"
            retried = service.store.start(accepted.deck_id, "retry", True)
            assert service.store.start(accepted.deck_id, "retry", True) == retried
            remote.lose_retry_response = True
            await service.reconcile_one()
            remote.fail_retry = True
            for _ in range(5):
                clock[0] += 40
                if service.store.operation(retried.operation_id).state == "failed":
                    break
                await service.reconcile_one()
            assert service.store.operation(retried.operation_id).state == "failed"
            resumed = service.store.start(accepted.deck_id, "retry-after-timeout-budget", True)
            remote.fail_retry = False
            clock[0] += 40
            await service.reconcile_one()
            assert service.store.operation(resumed.operation_id).phase == "poll"
            remote.fail_conversion = False
            clock[0] += 10
            await service.reconcile_one()
            assert service.store.deck(accepted.deck_id).state == "extracted"
            assert service.store.deck(accepted.deck_id).source_revision == 1
            assert len(remote.jobs) == 2
            assert service.store.operation(retried.operation_id).state == "failed"
            assert service.store.operation(resumed.operation_id).state == "succeeded"
            await service.extraction.close()
            service.store.close()
    asyncio.run(run())


def test_superseded_or_deleted_work_cannot_publish_and_cleanup_resumes(tmp_path: Path) -> None:
    async def run() -> None:
        remote = Remote()
        clock = [0.0]
        with patch("library_service.store.time.time", side_effect=lambda: clock[0]):
            service = library(tmp_path / "data", remote)
            accepted = service.store.upload(put_source(tmp_path), "team.pptx", "upload")
            await service.reconcile_one()
            clock[0] += 10
            obsolete = service.store.due()
            assert obsolete is not None
            reprocessed = service.store.start(accepted.deck_id, "reprocess", False)
            stale = Manifest.model_validate(manifest(accepted.deck_id, uuid4(), 1, remote.checksum, "team.pptx"))
            service.store.publish(obsolete, stale)
            assert service.store.deck(accepted.deck_id).generation_id is None
            assert service.store.deck(accepted.deck_id).source_revision == 2
            assert service.store.start(accepted.deck_id, "reprocess", False) == reprocessed
            deleted = service.store.delete(accepted.deck_id)
            assert service.store.delete(accepted.deck_id) == deleted
            service.store.publish(obsolete, stale)
            with pytest.raises(ServiceError) as denied:
                service.store.original(accepted.deck_id)
            assert denied.value.status == 410
            remote.fail_delete = True
            await service.reconcile_one()
            assert not service.store.source_path(accepted.deck_id).exists()
            assert service.store.deck(accepted.deck_id).state == "deleting"
            await service.extraction.close()
            service.store.close()
            remote.fail_delete = False
            service = library(tmp_path / "data", remote)
            clock[0] += 60
            await service.reconcile_one()
            deck = service.store.deck(accepted.deck_id)
            assert deck.state == "deleted" and deck.filename is None and deck.source_sha256 is None
            assert not service.store.decks()
            assert service.store.operation(deleted.operation_id).state == "succeeded"
            with pytest.raises(ServiceError) as blocked:
                service.store.start(accepted.deck_id, "new-process", False)
            assert blocked.value.status == 410
            await service.extraction.close()
            service.store.close()
    asyncio.run(run())


def test_asset_download_rechecks_deletion_after_await(tmp_path: Path) -> None:
    async def run() -> None:
        remote = Remote()
        clock = [0.0]
        with patch("library_service.store.time.time", side_effect=lambda: clock[0]):
            service = library(tmp_path / "data", remote)
            accepted = service.store.upload(put_source(tmp_path), "team.pptx", "upload")
            await service.reconcile_one()
            clock[0] += 10
            await service.reconcile_one()
            output = service.store.manifest(accepted.deck_id)
            remote.asset_started, remote.asset_continue = asyncio.Event(), asyncio.Event()
            task = asyncio.create_task(service.asset(accepted.deck_id, output.generation_id, output.assets[0].asset_id))
            await remote.asset_started.wait()
            service.store.delete(accepted.deck_id)
            remote.asset_continue.set()
            with pytest.raises(ServiceError) as denied:
                await task
            assert denied.value.status == 410
            await service.extraction.close()
            service.store.close()
    asyncio.run(run())


def test_delete_unknown_deck_id_creates_fence_for_backup_replay(tmp_path: Path) -> None:
    remote = Remote()
    service = library(tmp_path / "data", remote)
    identifier = uuid4()
    accepted = service.store.delete(identifier)
    assert service.store.delete(identifier) == accepted
    assert service.store.deck(identifier).state == "deleting"
    assert not service.store.source_path(identifier).exists()
    asyncio.run(service.reconcile_one())
    assert service.store.deck(identifier).state == "deleted"
    assert service.store.operation(accepted.operation_id).state == "succeeded"
    asyncio.run(service.extraction.close())
    service.store.close()


def test_manifest_validation_rejects_unknown_assets_and_preserves_old_snapshot(tmp_path: Path) -> None:
    value = manifest(uuid4(), uuid4(), 1, "0" * 64, "team.pptx")
    value["assets"] = []
    with pytest.raises(ValidationError):
        Manifest.model_validate(value)

    async def run() -> None:
        remote = Remote()
        clock = [0.0]
        with patch("library_service.store.time.time", side_effect=lambda: clock[0]):
            service = library(tmp_path / "data", remote)
            accepted = service.store.upload(put_source(tmp_path), "team.pptx", "upload")
            await service.reconcile_one()
            clock[0] += 10
            await service.reconcile_one()
            original = service.store.manifest(accepted.deck_id)
            service.store.start(accepted.deck_id, "reprocess", False)
            await service.reconcile_one()
            remote.corrupt_manifest = True
            clock[0] += 10
            await service.reconcile_one()
            assert service.store.deck(accepted.deck_id).state == "failed"
            assert service.store.manifest(accepted.deck_id).generation_id == original.generation_id
            await service.extraction.close()
            service.store.close()
    asyncio.run(run())


def test_notes_policy_is_authenticated_and_captured_for_upload(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "data", TOKEN, "http://extraction.test", EXTRACTION_TOKEN,
                        include_notes=False)
    app = create_app(settings, secure_storage=False, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/v1/policy").status_code == 401
        policy = client.get("/v1/policy", headers=HEADERS).json()
        assert policy["include_notes"] is False
        assert policy["search"]["state"] == "not_configured"
        accepted = client.post("/v1/decks", headers={**HEADERS, "idempotency-key": "upload"},
                               files={"file": ("team.pptx", b"deck")})
        assert accepted.status_code == 202
        operation = app.state.library.store._operation(UUID(accepted.json()["operation_id"]))
        assert operation.include_notes is False
        assert "include_notes" not in accepted.json()


def test_notes_contract_accepts_legacy_and_rejects_bad_states() -> None:
    legacy = Manifest.model_validate(manifest(uuid4(), uuid4(), 1, "0" * 64, "team.pptx"))
    assert legacy.content.slides[0].notes is None
    invalid = manifest(uuid4(), uuid4(), 1, "0" * 64, "team.pptx",
                       {"state": "excluded", "paragraphs": ["hidden note"], "truncated": False})
    with pytest.raises(ValidationError):
        Manifest.model_validate(invalid)


def test_search_intent_is_durable_and_deletion_queues_a_fence(tmp_path: Path) -> None:
    data = tmp_path / "library"
    store = Store(data, search_configured=True)
    source = data / "incoming" / "deck.pptx"
    source.write_bytes(b"deck")
    accepted = store.upload(source, "team.pptx", "upload-search")
    work = store.due()
    assert work is not None
    published = Manifest.model_validate(manifest(accepted.deck_id, uuid4(), 1,
        hashlib.sha256(b"deck").hexdigest(), "team.pptx"))
    payload = {"deck_id": str(accepted.deck_id), "slides": [{"slide_number": 1, "native_text": "Native text"}]}
    store.publish(work, published, payload, "stable-search-key", True)
    assert store.deck(accepted.deck_id).search is not None
    assert store.deck(accepted.deck_id).search.state == "queued"
    store.close()

    restored = Store(data, search_configured=True)
    queued = restored.search_work()
    assert queued is not None and queued[2] == "stable-search-key"
    assert queued[1] == payload
    failed = SearchStatus(state="failed", index_revision=1, generation_id=published.generation_id,
                         profile_id="profile", job_id=uuid4(), error=ErrorDetail(
                             code="embedding_unavailable", message="Retry safely.", retryable=True))
    restored.search_submitted(accepted.deck_id, failed)
    retry = restored.retry_search(accepted.deck_id, "search-retry-key")
    assert retry.state == "queued" and retry.error is None
    assert restored.retry_search(accepted.deck_id, "search-retry-key").state == "queued"
    retry_work = restored.search_work()
    assert retry_work is not None and retry_work[4] is not None
    restored.delete(accepted.deck_id)
    fence = restored.search_work()
    assert fence is not None and fence[0] == accepted.deck_id and fence[3].state == "deleting"
    restored.close()


def test_malformed_notes_failure_is_actionable_and_redacted(tmp_path: Path) -> None:
    async def run() -> None:
        remote = Remote()
        remote.fail_notes = True
        service = library(tmp_path / "data", remote)
        accepted = service.store.upload(put_source(tmp_path), "team.pptx", "upload")
        await service.reconcile_one()
        await asyncio.sleep(0.02)
        await service.reconcile_one()
        deck = service.store.deck(accepted.deck_id)
        assert deck.state == "failed"
        assert deck.error is not None
        assert deck.error.code == "invalid_notes"
        assert deck.error.message == "Speaker notes could not be read. Correct the presentation and upload it again."
        assert not deck.error.retryable
        await service.extraction.close()
        service.store.close()

    asyncio.run(run())


def test_library_rejects_manifest_that_ignores_captured_notes_policy(tmp_path: Path) -> None:
    async def run() -> None:
        remote = Remote()
        remote.mismatch_notes_policy = True
        service = library(tmp_path / "data", remote)
        accepted = service.store.upload(put_source(tmp_path), "team.pptx", "upload")
        await service.reconcile_one()
        await asyncio.sleep(0.02)
        await service.reconcile_one()
        deck = service.store.deck(accepted.deck_id)
        assert deck.state == "failed"
        assert deck.generation_id is None
        await service.extraction.close()
        service.store.close()

    asyncio.run(run())


def test_http_auth_upload_download_and_delete_boundaries(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "data", TOKEN, "http://extraction.test", EXTRACTION_TOKEN,
                        upload_limit=64, request_limit=1024)
    with TestClient(create_app(settings, secure_storage=False, start_worker=False)) as client:
        consumed = False

        def bytes_body() -> Iterator[bytes]:
            nonlocal consumed
            consumed = True
            yield b"x" * 2000

        assert client.post("/v1/decks", content=bytes_body()).status_code == 401
        assert not consumed
        assert client.post("/v1/decks", content=bytes_body(), headers=HEADERS).status_code == 413
        response = client.post("/v1/decks", headers={**HEADERS, "Idempotency-Key": "upload"},
                               files={"file": ("C:\\private\\team.pptx", b"deck")})
        assert response.status_code == 202
        accepted = response.json()
        deck_id = accepted["deck_id"]
        assert client.get(f"/v1/decks/{deck_id}", headers=HEADERS).json()["filename"] == "team.pptx"
        original = client.get(f"/v1/decks/{deck_id}/original", headers=HEADERS)
        assert original.content == b"deck"
        assert original.headers["etag"] == f'"{hashlib.sha256(b"deck").hexdigest()}"'
        assert original.headers["cache-control"] == "no-store"
        assert "private" not in original.headers["content-disposition"]
        assert client.get(f"/v1/decks/{deck_id}/original").status_code == 401
        assert client.delete(f"/v1/decks/{deck_id}", headers=HEADERS).status_code == 202
        assert client.get(f"/v1/decks/{deck_id}/original", headers=HEADERS).status_code == 410
        assert client.get(f"/v1/decks/{deck_id}/manifest", headers=HEADERS).status_code == 410
        deletions = client.get("/v1/deletions", headers=HEADERS)
        assert deletions.status_code == 200
        assert deletions.json()[0]["deck_id"] == deck_id
        assert deletions.json()[0]["state"] == "deleting"
        assert set(deletions.json()[0]) == {"deck_id", "state", "operation_id", "updated_at"}
        assert client.get("/v1/decks", headers=HEADERS).json() == []
        assert client.get("/v1/deletions").status_code == 401
        assert client.get("/v1/decks/not-a-uuid", headers=HEADERS).status_code == 400
        assert client.get("/openapi.json", headers=HEADERS).status_code == 404
        assert "traceback" not in json.dumps(client.get("/v1/decks/not-a-uuid", headers=HEADERS).json()).lower()


def test_storage_has_one_process_owner_and_api_limits(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    with pytest.raises(RuntimeError, match="already has an owner"):
        Store(tmp_path / "data")
    store.close()
    settings = Settings(tmp_path / "other", TOKEN, "http://extraction.test", EXTRACTION_TOKEN,
                        upload_limit=4, request_limit=1024)
    with TestClient(create_app(settings, secure_storage=False, start_worker=False)) as client:
        assert client.post("/v1/decks", headers={**HEADERS, "Idempotency-Key": "bad key"}, files={"file": ("team.pptx", b"deck")}).status_code == 400
        assert client.post("/v1/decks", headers={**HEADERS, "Idempotency-Key": "upload"}, files={"file": ("team.pptx", b"longer")}).status_code == 413
        assert client.post("/v1/decks", headers={**HEADERS, "Idempotency-Key": "upload"}, files={"file": ("team.ppt", b"deck")}).status_code == 415


def test_processing_outage_has_bounded_retries_and_asset_integrity_failure_is_safe(tmp_path: Path) -> None:
    async def run() -> None:
        async def unavailable(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "private details must not be returned"})

        clock = [0.0]
        with patch("library_service.store.time.time", side_effect=lambda: clock[0]):
            store = Store(tmp_path / "data")
            extraction = Extraction("http://extraction.test", EXTRACTION_TOKEN,
                                    transport=httpx.MockTransport(unavailable))
            service = Library(store, extraction, retry_limit=3)
            accepted = store.upload(put_source(tmp_path), "team.pptx", "upload")
            for _ in range(3):
                assert await service.reconcile_one()
                clock[0] += 60
            deck = store.deck(accepted.deck_id)
            assert deck.state == "failed" and deck.error is not None and deck.error.retryable
            assert "private" not in deck.error.message
            assert not await service.reconcile_one()
            await extraction.close()
            store.close()

        output = Manifest.model_validate(manifest(uuid4(), uuid4(), 1, "0" * 64, "team.pptx"))

        async def corrupt(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"wrong bytes", headers={"content-type": "image/png", "etag": f'"{output.assets[0].sha256}"'})

        extraction = Extraction("http://extraction.test", EXTRACTION_TOKEN,
                                transport=httpx.MockTransport(corrupt))
        with pytest.raises(UpstreamError) as error:
            await extraction.asset(output.assets[0])
        assert error.value.detail.code == "invalid_extraction_response"
        await extraction.close()

        async def hanging(request: httpx.Request) -> httpx.Response:
            await asyncio.Event().wait()
            return httpx.Response(200)

        extraction = Extraction("http://extraction.test", EXTRACTION_TOKEN, timeout=0.01,
                                transport=httpx.MockTransport(hanging))
        with pytest.raises(UpstreamError) as timeout:
            await extraction.job(uuid4())
        assert timeout.value.detail.retryable
        await extraction.close()
    asyncio.run(run())


def test_queue_saturation_waits_without_losing_accepted_work(tmp_path: Path) -> None:
    async def run() -> None:
        remote = Remote()
        busy = [True]

        async def handler(request: httpx.Request) -> httpx.Response:
            if busy[0]:
                return httpx.Response(429, json={"error": "queue full"})
            return await remote(request)

        clock = [0.0]
        with patch("library_service.store.time.time", side_effect=lambda: clock[0]):
            store = Store(tmp_path / "data")
            extraction = Extraction("http://extraction.test", EXTRACTION_TOKEN,
                                    transport=httpx.MockTransport(handler))
            service = Library(store, extraction, poll_interval=0.01, retry_limit=2)
            accepted = store.upload(put_source(tmp_path), "team.pptx", "upload")
            for _ in range(5):
                assert await service.reconcile_one()
                clock[0] += 60
            assert store.deck(accepted.deck_id).state == "queued"
            busy[0] = False
            await service.reconcile_one()
            clock[0] += 60
            await service.reconcile_one()
            assert store.deck(accepted.deck_id).state == "extracted"
            assert len(remote.jobs) == 1
            await extraction.close()
            store.close()
    asyncio.run(run())


def test_corrupt_persisted_content_returns_safe_error(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "data", TOKEN, "http://extraction.test", EXTRACTION_TOKEN)
    app = create_app(settings, secure_storage=False, start_worker=False)
    with TestClient(app) as client:
        accepted = client.post("/v1/decks", headers={**HEADERS, "Idempotency-Key": "upload"},
                               files={"file": ("team.pptx", b"deck")}).json()
        corrupt_sqlite_deck(settings.data_dir, accepted["deck_id"])
        response = client.get(accepted["status_url"], headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "metadata_unavailable"
        assert "private_slide_text" not in response.text


def test_startup_failure_after_store_creation_releases_owner_lock(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(tmp_path / "data", TOKEN, "http://extraction.test", EXTRACTION_TOKEN)

    def fail_extraction(*args, **kwargs):
        raise RuntimeError("injected extraction setup failure")

    monkeypatch.setattr("library_service.api.Extraction", fail_extraction)
    with pytest.raises(RuntimeError, match="injected extraction setup failure"):
        with TestClient(create_app(settings, secure_storage=False, start_worker=False)):
            pass

    store = Store(settings.data_dir)
    store.close()
