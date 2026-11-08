from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import model_validator

from .models import Accepted, Deck, DeckState, DeletionStatus, ErrorDetail, Job, Manifest, Operation, Record, ServiceError
from .metadata import Metadata


def now() -> str:
    return datetime.now(UTC).isoformat()


class StoredDeck(Record):
    deck_id: UUID
    filename: str | None
    checksum: str | None
    byte_length: int | None
    source_revision: int
    fence: int = 0
    state: DeckState = "queued"
    current_operation: UUID
    deleted_at: str | None = None
    manifest: Manifest | None = None
    updated_at: str

    @model_validator(mode="after")
    def deletion_state(self) -> StoredDeck:
        if (self.deleted_at is None) == (self.state in ("deleting", "deleted")):
            raise ValueError("Deletion timestamp and state do not match")
        return self


class StoredOperation(Operation):
    fence: int
    remote_key: str
    parent_job: UUID | None = None
    job: Job | None = None
    retries: int = 0
    due: float

    def public(self) -> Operation:
        return Operation.model_validate(self.model_dump(exclude={
            "fence", "remote_key", "parent_job", "job", "retries", "due"
        }))


class Work(Record):
    deck: StoredDeck
    operation: StoredOperation


class Store:
    def __init__(self, root: Path, backend: Literal["sqlite", "postgresql"] = "sqlite",
                 dsn: str | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.lock_file = (self.root / "owner.lock").open("a+b")
        if self.lock_file.tell() == 0:
            self.lock_file.write(b"0")
            self.lock_file.flush()
        self.lock_file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise RuntimeError("Library data directory already has an owner") from None
        self.metadata: Metadata | None = None
        try:
            self.metadata = Metadata(self.root, self._validate_legacy_records, backend, dsn)
            for name in ("incoming", "originals"):
                (self.root / name).mkdir(exist_ok=True, mode=0o700)
            self.recover()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _validate_legacy_records(metadata: Metadata) -> None:
        try:
            for raw in metadata.deck_records():
                StoredDeck.model_validate_json(raw)
            for raw in metadata.operation_records():
                StoredOperation.model_validate_json(raw)
        except ValueError:
            raise RuntimeError("Library metadata failed validation. Restore a consistent backup.") from None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            assert self.metadata is not None
            with self.metadata.transaction():
                yield

    def close(self) -> None:
        if self.metadata is not None:
            self.metadata.close()
        if not self.lock_file.closed:
            if sys.platform == "win32":
                import msvcrt
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            self.lock_file.close()

    def storage_ready(self) -> bool:
        return self.metadata is not None and self.metadata.check_health()

    def source_path(self, deck_id: UUID) -> Path:
        return self.root / "originals" / f"{deck_id}.pptx"

    def recover(self) -> None:
        for path in (self.root / "incoming").iterdir():
            if path.is_file():
                path.unlink()
        assert self.metadata is not None
        known = set(self.metadata.deck_ids())
        for path in (self.root / "originals").glob("*.pptx"):
            if path.stem not in known:
                path.unlink()

    def _deck(self, identifier: UUID, allow_deleted: bool = False) -> StoredDeck:
        assert self.metadata is not None
        raw = self.metadata.deck_record(str(identifier))
        if raw is None:
            raise ServiceError(404, "not_found", "Deck is unavailable.")
        try:
            deck = StoredDeck.model_validate_json(raw)
        except ValueError:
            raise ServiceError(503, "metadata_unavailable", "Library metadata failed validation. Restore a consistent backup.") from None
        if deck.deleted_at is not None and not allow_deleted:
            raise ServiceError(410, "deleted", "Deck identity was deleted.")
        return deck

    def _operation(self, identifier: UUID) -> StoredOperation:
        assert self.metadata is not None
        raw = self.metadata.operation_record(str(identifier))
        if raw is None:
            raise ServiceError(404, "not_found", "Operation is unavailable.")
        try:
            return StoredOperation.model_validate_json(raw)
        except ValueError:
            raise ServiceError(503, "metadata_unavailable", "Library operation metadata failed validation. Restore a consistent backup.") from None

    def _save_deck(self, deck: StoredDeck) -> None:
        assert self.metadata is not None
        self.metadata.save_deck(str(deck.deck_id), deck.model_dump_json())

    def _save_operation(self, operation: StoredOperation) -> None:
        assert self.metadata is not None
        self.metadata.save_operation(str(operation.operation_id), str(operation.deck_id),
                                     operation.state, operation.due, operation.model_dump_json())

    def _request(self, key: str, digest: str) -> StoredOperation | None:
        assert self.metadata is not None
        row = self.metadata.request(key)
        if row is None:
            return None
        operation = self._operation(UUID(row[1]))
        self._deck(operation.deck_id)
        if row[0] != digest:
            raise ServiceError(409, "idempotency_conflict", "Idempotency key was used for different input.")
        return operation

    def _new_operation(self, deck_id: UUID, revision: int, fence: int,
                       kind: Literal["extract", "retry", "delete"],
                       phase: Literal["submit", "retry", "poll", "delete"] = "submit") -> StoredOperation:
        identifier = uuid4()
        timestamp = now()
        return StoredOperation(operation_id=identifier, deck_id=deck_id, kind=kind,
                               state="pending", phase=phase, source_revision=revision,
                               fence=fence, remote_key=str(identifier), created_at=timestamp,
                               updated_at=timestamp, due=time.time(), status_url=f"/v1/operations/{identifier}")

    @staticmethod
    def accepted(operation: StoredOperation) -> Accepted:
        return Accepted(deck_id=operation.deck_id, operation_id=operation.operation_id,
                        status_url=f"/v1/decks/{operation.deck_id}",
                        operation_url=operation.status_url)

    def upload(self, source: Path, filename: str, key: str) -> Accepted:
        with source.open("rb") as handle:
            checksum = hashlib.file_digest(handle, "sha256").hexdigest()
        byte_length = source.stat().st_size
        digest = hashlib.sha256(f"upload:{filename}:{checksum}".encode()).hexdigest()
        with self.transaction():
            previous = self._request(key, digest)
            if previous is not None:
                return self.accepted(previous)
            identifier = uuid4()
            operation = self._new_operation(identifier, 1, 0, "extract")
            deck = StoredDeck(deck_id=identifier, filename=filename, checksum=checksum,
                              byte_length=byte_length, source_revision=1,
                              current_operation=operation.operation_id, updated_at=now())
            os.replace(source, self.source_path(identifier))
            self._save_deck(deck)
            self._save_operation(operation)
            assert self.metadata is not None
            self.metadata.save_request(key, digest, str(operation.operation_id))
            return self.accepted(operation)

    def deck(self, identifier: UUID) -> Deck:
        with self.lock:
            deck = self._deck(identifier, allow_deleted=True)
            operation = self._operation(deck.current_operation)
            available = deck.manifest is not None and deck.deleted_at is None
            return Deck(deck_id=identifier, filename=deck.filename, source_sha256=deck.checksum,
                        byte_length=deck.byte_length, source_revision=deck.source_revision,
                        state=deck.state, operation_id=operation.operation_id,
                        generation_id=deck.manifest.generation_id if available and deck.manifest else None,
                        completed_slides=operation.completed_slides, total_slides=operation.total_slides,
                        stage=operation.stage, error=operation.error, warnings=operation.warnings,
                        status_url=f"/v1/decks/{identifier}",
                        manifest_url=f"/v1/decks/{identifier}/manifest" if available else None,
                        original_url=f"/v1/decks/{identifier}/original" if deck.deleted_at is None else None,
                        updated_at=deck.updated_at)

    def decks(self) -> list[Deck]:
        with self.lock:
            assert self.metadata is not None
            identifiers = [UUID(identifier) for identifier in self.metadata.deck_ids(newest_first=True)]
            return [deck for identifier in identifiers if (deck := self.deck(identifier)).state not in ("deleting", "deleted")]

    def deletions(self) -> list[DeletionStatus]:
        with self.lock:
            assert self.metadata is not None
            result: list[DeletionStatus] = []
            for raw_record in self.metadata.deck_records(newest_first=True):
                deck = StoredDeck.model_validate_json(raw_record)
                if deck.deleted_at is not None and deck.state in ("deleting", "deleted"):
                    result.append(DeletionStatus(deck_id=deck.deck_id, state=deck.state,
                                                 operation_id=deck.current_operation, updated_at=deck.updated_at))
            return result

    def operation(self, identifier: UUID) -> Operation:
        with self.lock:
            return self._operation(identifier).public()

    def start(self, identifier: UUID, key: str, retry: bool) -> Accepted:
        digest = hashlib.sha256(f"{'retry' if retry else 'reprocess'}:{identifier}".encode()).hexdigest()
        with self.transaction():
            previous = self._request(key, digest)
            if previous is not None:
                if previous.deck_id != identifier:
                    raise ServiceError(409, "idempotency_conflict", "Idempotency key was used for another deck.")
                return self.accepted(previous)
            deck = self._deck(identifier)
            old = self._operation(deck.current_operation)
            if retry:
                if old.state != "failed" or old.error is None or not old.error.retryable:
                    raise ServiceError(409, "not_retryable", "Only the current retryable failed operation can be retried.")
                remote_failed = old.job is not None and old.job.state == "failed"
                remote_retry = old.phase == "retry" and old.parent_job is not None
                phase = "retry" if remote_failed or remote_retry else ("poll" if old.job else "submit")
                operation = self._new_operation(identifier, deck.source_revision, deck.fence + 1,
                                                "retry", cast(Literal["submit", "retry", "poll"], phase))
                operation.parent_job = old.job.job_id if remote_failed and old.job else old.parent_job if remote_retry else None
                operation.job = None if remote_failed or remote_retry else old.job
                if not remote_failed:
                    operation.remote_key = old.remote_key
            else:
                if deck.source_revision >= 9_999_999_999:
                    raise ServiceError(409, "revision_limit", "Deck has reached the source revision limit.")
                deck.source_revision += 1
                operation = self._new_operation(identifier, deck.source_revision, deck.fence + 1, "extract")
            if old.state in ("pending", "processing"):
                old.state, old.updated_at = "cancelled", now()
                self._save_operation(old)
            deck.fence += 1
            deck.current_operation, deck.state, deck.updated_at = operation.operation_id, "queued", now()
            self._save_operation(operation)
            self._save_deck(deck)
            assert self.metadata is not None
            self.metadata.save_request(key, digest, str(operation.operation_id))
            return self.accepted(operation)

    def delete(self, identifier: UUID) -> Accepted:
        with self.transaction():
            assert self.metadata is not None
            if not self.metadata.has_deck(str(identifier)):
                timestamp = now()
                operation = self._new_operation(identifier, 1, 1, "delete", "delete")
                deck = StoredDeck(deck_id=identifier, filename=None, checksum=None, byte_length=None,
                                  source_revision=1, fence=1, state="deleting",
                                  current_operation=operation.operation_id, deleted_at=timestamp,
                                  updated_at=timestamp)
                self._save_deck(deck)
                self._save_operation(operation)
                return self.accepted(operation)
            deck = self._deck(identifier, allow_deleted=True)
            if deck.deleted_at is not None:
                return self.accepted(self._operation(deck.current_operation))
            old = self._operation(deck.current_operation)
            if old.state in ("pending", "processing"):
                old.state, old.updated_at = "cancelled", now()
                self._save_operation(old)
            deck.fence += 1
            operation = self._new_operation(identifier, deck.source_revision, deck.fence, "delete", "delete")
            deck.deleted_at, deck.state, deck.updated_at = now(), "deleting", now()
            deck.manifest, deck.current_operation = None, operation.operation_id
            deck.filename, deck.checksum, deck.byte_length = None, None, None
            for operation_id in self.metadata.operation_ids(str(identifier)):
                previous = self._operation(UUID(operation_id))
                previous.job, previous.error, previous.warnings = None, None, []
                previous.remote_key, previous.parent_job = "", None
                previous.completed_slides, previous.total_slides, previous.stage = 0, 0, "deleted"
                if previous.state in ("pending", "processing"):
                    previous.state = "cancelled"
                self._save_operation(previous)
            self._save_operation(operation)
            self._save_deck(deck)
            self.metadata.delete_requests(str(identifier))
            return self.accepted(operation)

    def due(self) -> Work | None:
        with self.lock:
            assert self.metadata is not None
            pending = [self._operation(UUID(identifier))
                       for identifier in self.metadata.due_operation_ids(time.time())]
            pending.sort(key=lambda operation: operation.kind != "delete")
            for operation in pending:
                deck = self._deck(operation.deck_id, allow_deleted=True)
                if deck.current_operation == operation.operation_id and deck.fence == operation.fence:
                    return Work(deck=deck, operation=operation)
            return None

    def _current(self, work: Work) -> tuple[StoredDeck, StoredOperation] | None:
        deck = self._deck(work.deck.deck_id, allow_deleted=True)
        if (deck.current_operation != work.operation.operation_id or deck.fence != work.operation.fence
                or deck.source_revision != work.operation.source_revision
                or (deck.deleted_at is not None and work.operation.kind != "delete")):
            return None
        return deck, self._operation(work.operation.operation_id)

    def accept_job(self, work: Work, job: Job, poll_interval: float) -> None:
        with self.transaction():
            current = self._current(work)
            if current is None:
                return
            deck, operation = current
            operation.job, operation.phase, operation.state = job, "poll", "processing"
            operation.completed_slides, operation.total_slides = job.completed_slides, job.total_slides
            operation.stage, operation.warnings = job.stage, job.warnings
            operation.error, operation.retries = None, 0
            operation.due, operation.updated_at = time.time() + poll_interval, now()
            deck.state, deck.updated_at = "processing", now()
            self._save_operation(operation)
            self._save_deck(deck)

    def publish(self, work: Work, manifest: Manifest) -> None:
        with self.transaction():
            current = self._current(work)
            if current is None:
                return
            deck, operation = current
            deck.manifest, deck.state, deck.updated_at = manifest, "extracted", now()
            operation.state, operation.phase, operation.updated_at = "succeeded", "complete", now()
            operation.completed_slides = operation.total_slides = len(manifest.content.slides)
            operation.stage, operation.error, operation.warnings = "extracted", None, manifest.warnings
            self._save_operation(operation)
            self._save_deck(deck)

    def fail(self, work: Work, error: ErrorDetail, job: Job | None = None) -> None:
        with self.transaction():
            current = self._current(work)
            if current is None:
                return
            deck, operation = current
            operation.state, operation.error, operation.updated_at = "failed", error, now()
            if job is not None:
                operation.job = job
                operation.completed_slides, operation.total_slides = job.completed_slides, job.total_slides
                operation.stage, operation.warnings = job.stage, job.warnings
            deck.state, deck.updated_at = "failed", now()
            self._save_operation(operation)
            self._save_deck(deck)

    def defer(self, work: Work, error: ErrorDetail, retry_limit: int) -> None:
        with self.transaction():
            current = self._current(work)
            if current is None:
                return
            deck, operation = current
            operation.retries += 1
            operation.error, operation.updated_at = error, now()
            operation.due = time.time() + min(30, 2 ** min(operation.retries, 5))
            if operation.kind != "delete" and error.code != "extraction_busy" and (not error.retryable or operation.retries >= retry_limit):
                operation.state, deck.state = "failed", "failed"
            deck.updated_at = now()
            self._save_operation(operation)
            self._save_deck(deck)

    def remove_original(self, work: Work) -> None:
        with self.lock:
            if self._current(work) is not None and work.operation.kind == "delete":
                self.source_path(work.deck.deck_id).unlink(missing_ok=True)

    def finish_deletion(self, work: Work) -> None:
        with self.transaction():
            current = self._current(work)
            if current is None or self.source_path(work.deck.deck_id).exists():
                return
            deck, operation = current
            deck.state, deck.filename, deck.checksum, deck.byte_length = "deleted", None, None, None
            deck.manifest, deck.updated_at = None, now()
            operation.state, operation.phase, operation.stage = "succeeded", "complete", "deleted"
            operation.error, operation.updated_at = None, now()
            self._save_operation(operation)
            self._save_deck(deck)

    def manifest(self, identifier: UUID, generation: UUID | None = None) -> Manifest:
        with self.lock:
            deck = self._deck(identifier)
            if deck.manifest is None or (generation is not None and deck.manifest.generation_id != generation):
                raise ServiceError(404, "not_found", "Generation is unavailable. Refresh the deck status.")
            result = deck.manifest.model_copy(deep=True)
            result.canonical_original_reference = f"/v1/decks/{identifier}/original"
            for asset in result.assets:
                asset.download_url = f"/v1/decks/{identifier}/generations/{result.generation_id}/assets/{asset.asset_id}"
            return result

    def asset(self, identifier: UUID, generation: UUID, asset_id: UUID) -> tuple[Manifest, str]:
        manifest = self.manifest(identifier, generation)
        asset = next((asset for asset in manifest.assets if asset.asset_id == asset_id), None)
        if asset is None:
            raise ServiceError(404, "not_found", "Asset is unavailable.")
        slide_number = next((slide.source.number for slide in manifest.content.slides
                             if slide.preview.asset_local_id == asset.local_id
                             or any(item.original_asset_local_id == asset.local_id
                                    or item.rendition.asset_local_id == asset.local_id
                                    for item in slide.occurrences)), 0)
        return manifest, f"{Path(manifest.source_filename).stem}-slide-{slide_number}"

    def original(self, identifier: UUID) -> tuple[bytes, str, str]:
        with self.lock:
            deck = self._deck(identifier)
            try:
                data = self.source_path(identifier).read_bytes()
            except OSError:
                raise ServiceError(503, "original_unavailable", "Canonical original is unavailable. Restore the library backup.") from None
            if len(data) != deck.byte_length or hashlib.sha256(data).hexdigest() != deck.checksum:
                raise ServiceError(503, "original_unavailable", "Canonical original failed its integrity check.")
            return data, deck.filename or "presentation.pptx", deck.checksum or ""
