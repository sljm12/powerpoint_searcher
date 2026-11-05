from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from PIL import Image

from .models import (
    Asset,
    Claim,
    Deletion,
    ErrorDetail,
    ExtractionResult,
    Job,
    Manifest,
    ServiceError,
    Stage,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    """One metadata owner. All authorization and publication changes share this transaction lock."""

    def __init__(self, root: Path, queue_limit: int = 8, manifest_limit: int = 8 * 1024 * 1024) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.queue_limit = queue_limit
        self.manifest_limit = manifest_limit
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
            raise RuntimeError("Data directory already has an owner") from None
        self.db = sqlite3.connect(self.root / "metadata.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.close()
            raise RuntimeError("Unsupported metadata schema version")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS decks (
                id TEXT PRIMARY KEY, revision INTEGER, checksum TEXT, filename TEXT, fence INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0, cleanup TEXT, cleanup_error TEXT,
                current_job TEXT, active_generation TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, deck TEXT NOT NULL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (
                deck TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, job TEXT NOT NULL,
                PRIMARY KEY(deck, key)
            );
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY, deck TEXT NOT NULL, manifest TEXT NOT NULL, directory TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, deck TEXT NOT NULL, generation TEXT NOT NULL,
                relative_path TEXT NOT NULL, record TEXT NOT NULL
            );
            PRAGMA user_version=1;
        """)
        self.recover()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self) -> None:
        if hasattr(self, "db"):
            self.db.close()
        if not self.lock_file.closed:
            if sys.platform == "win32":
                import msvcrt
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            self.lock_file.close()

    def _deck(self, deck_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "not_found", "Deck is unavailable.")
        if row["deleted"]:
            raise ServiceError(410, "deleted", "Deck identity was deleted.")
        return cast(sqlite3.Row, row)

    def _save_job(self, job: Job) -> None:
        self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)",
                        (str(job.job_id), str(job.deck_id), job.model_dump_json()))

    def get_job(self, job_id: UUID) -> Job:
        with self.lock:
            row = self.db.execute("SELECT record FROM jobs WHERE id=?", (str(job_id),)).fetchone()
            if row is None:
                raise ServiceError(404, "not_found", "Job is unavailable.")
            return Job.model_validate_json(row[0])

    def _queue_space(self, replacing_deck: str | None = None) -> None:
        records = self.db.execute("SELECT record FROM jobs WHERE deck != ?", (replacing_deck or "",)).fetchall()
        if sum(Job.model_validate_json(r[0]).state in ("queued", "processing") for r in records) >= self.queue_limit:
            raise ServiceError(429, "queue_full", "Processing queue is full; retry later.", True)

    def _new_job(self, deck: UUID, revision: int, attempt: int) -> Job:
        identifier = uuid4()
        return Job(job_id=identifier, deck_id=deck, source_revision=revision,
                   generation_id=uuid4(), state="queued", stage="queued", attempt=attempt,
                   completed_slides=0, total_slides=0, created_at=now(), updated_at=now(),
                   status_url=f"/v1/jobs/{identifier}")

    def submit(self, deck: UUID, revision: int, key: str, source: Path, filename: str = "presentation.pptx") -> Job:
        if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename) or "/" in filename or "\\" in filename:
            raise ServiceError(400, "malformed_request", "Source filename is invalid.")
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        digest = hashlib.sha256(f"v1:{revision}:{checksum}:{filename}".encode()).hexdigest()
        deck_id = str(deck)
        with self.transaction():
            row = self.db.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
            if row is not None and row["deleted"]:
                raise ServiceError(410, "deleted", "Deck identity was deleted.")
            previous = self.db.execute("SELECT * FROM requests WHERE deck=? AND key=?", (deck_id, key)).fetchone()
            if previous is not None:
                if previous["digest"] != digest:
                    raise ServiceError(409, "idempotency_conflict", "Idempotency key was used for different input.")
                return self.get_job(UUID(previous["job"]))
            if row is not None:
                if revision < row["revision"]:
                    raise ServiceError(409, "stale_revision", "Source revision has been superseded.")
                if revision == row["revision"]:
                    if checksum != row["checksum"] or filename != row["filename"]:
                        raise ServiceError(409, "revision_conflict", "Revision already contains different bytes.")
                    job = self.get_job(UUID(row["current_job"]))
                    self.db.execute("INSERT INTO requests VALUES (?, ?, ?, ?)", (deck_id, key, digest, str(job.job_id)))
                    return job
            self._queue_space(deck_id)
            if row is not None:
                for record in self.db.execute("SELECT record FROM jobs WHERE deck=?", (deck_id,)).fetchall():
                    old_job = Job.model_validate_json(record[0])
                    if old_job.state in ("queued", "processing"):
                        old_job.state = "cancelled"
                        old_job.updated_at = now()
                        self._save_job(old_job)
            directory = self.root / deck_id
            directory.mkdir(exist_ok=True)
            destination = directory / f"source-{revision}.pptx"
            shutil.copyfile(source, destination)
            job = self._new_job(deck, revision, 1)
            self.db.execute("""INSERT INTO decks (id, revision, checksum, filename, current_job) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, checksum=excluded.checksum,
                filename=excluded.filename, current_job=excluded.current_job, fence=decks.fence+1""",
                (deck_id, revision, checksum, filename, str(job.job_id)))
            self._save_job(job)
            self.db.execute("INSERT INTO requests VALUES (?, ?, ?, ?)", (deck_id, key, digest, str(job.job_id)))
            return job

    def retry(self, identifier: UUID) -> Job:
        with self.transaction():
            job = self.get_job(identifier)
            deck = self._deck(str(job.deck_id))
            if deck["current_job"] != str(identifier) or deck["revision"] != job.source_revision:
                raise ServiceError(409, "superseded", "Job has been superseded.")
            if job.state != "failed" or job.error is None or not job.error.retryable:
                raise ServiceError(409, "not_retryable", "Only a current retryable failed job can be retried.")
            self._queue_space()
            new_job = self._new_job(job.deck_id, job.source_revision, job.attempt + 1)
            self._save_job(new_job)
            self.db.execute("UPDATE decks SET current_job=?, fence=fence+1 WHERE id=?", (str(new_job.job_id), str(job.deck_id)))
            return new_job

    def claim(self) -> Claim | None:
        with self.transaction():
            for record in self.db.execute("SELECT record FROM jobs ORDER BY rowid").fetchall():
                job = Job.model_validate_json(record[0])
                if job.state != "queued":
                    continue
                deck = self._deck(str(job.deck_id))
                if deck["current_job"] != str(job.job_id):
                    continue
                job.state, job.stage, job.updated_at = "processing", "extracting", now()
                self._save_job(job)
                output = self.root / str(job.deck_id) / "attempts" / str(job.generation_id)
                output.mkdir(parents=True, exist_ok=False)
                return Claim(job=job, fence=deck["fence"],
                             source_path=self.root / str(job.deck_id) / f"source-{job.source_revision}.pptx",
                             output_path=output)
        return None

    def is_current(self, claim: Claim) -> bool:
        with self.lock:
            row = self.db.execute("SELECT * FROM decks WHERE id=?", (str(claim.job.deck_id),)).fetchone()
            return bool(row and not row["deleted"] and row["fence"] == claim.fence
                        and row["current_job"] == str(claim.job.job_id)
                        and row["revision"] == claim.job.source_revision)

    def fail(self, claim: Claim, code: str, message: str, retryable: bool = True) -> None:
        with self.transaction():
            if not self.is_current(claim):
                return
            job = self.get_job(claim.job.job_id)
            if job.state != "processing":
                return
            job.state, job.updated_at = "failed", now()
            job.error = ErrorDetail(code=code, message=message, retryable=retryable, stage=job.stage)
            self._save_job(job)

    def update_progress(self, claim: Claim, stage: Stage, completed: int, total: int) -> None:
        if (stage not in ("extracting", "rendering") or type(completed) is not int or type(total) is not int
                or not 0 <= completed <= total <= 100):
            raise ValueError("Invalid extraction progress")
        with self.transaction():
            if not self.is_current(claim):
                return
            job = self.get_job(claim.job.job_id)
            if job.state != "processing":
                return
            if job.stage == "rendering" and stage == "extracting":
                raise ValueError("Progress stage cannot move backwards")
            job.stage, job.completed_slides, job.total_slides = stage, completed, total
            job.updated_at = now()
            self._save_job(job)

    def publish(self, claim: Claim, result: ExtractionResult) -> bool:
        if not self.is_current(claim):
            return False
        result = ExtractionResult.model_validate(result)
        assets: list[Asset] = []
        files: list[tuple[str, str, str]] = []
        local_ids: set[str] = set()
        if len(result.assets) > 10000:
            raise ValueError("Too many output assets")
        total_bytes = 0
        for output in result.assets:
            path = claim.output_path / output.relative_path
            if path.is_symlink() or not path.resolve().is_relative_to(claim.output_path.resolve()) or not path.is_file():
                raise ValueError("Invalid output asset")
            if output.local_id in local_ids:
                raise ValueError("Duplicate output asset identity")
            local_ids.add(output.local_id)
            total_bytes += path.stat().st_size
            if path.stat().st_size > 32 * 1024 * 1024 or total_bytes > 256 * 1024 * 1024:
                raise ValueError("Output asset bytes exceed configured limits")
            data = path.read_bytes()
            if output.media_type.startswith("image/") and output.media_type != "image/svg+xml":
                with Image.open(BytesIO(data)) as image:
                    if image.size != output.pixel_size or image.width * image.height > 16_000_000:
                        raise ValueError("Output raster dimensions are invalid")
                    image.verify()
            identifier = uuid4()
            asset = Asset(asset_id=identifier, local_id=output.local_id, media_type=output.media_type,
                          role=output.role, pixel_size=output.pixel_size, byte_length=len(data), sha256=hashlib.sha256(data).hexdigest(),
                          download_url=f"/v1/assets/{identifier}")
            assets.append(asset)
            files.append((str(identifier), output.relative_path, asset.model_dump_json()))
        with self.transaction():
            if not self.is_current(claim):
                return False
            deck = self._deck(str(claim.job.deck_id))
            job = self.get_job(claim.job.job_id)
            if job.state != "processing" or job.generation_id != claim.job.generation_id:
                return False
            manifest = Manifest(deck_id=job.deck_id, source_revision=job.source_revision,
                                generation_id=job.generation_id, source_sha256=deck["checksum"],
                                source_filename=deck["filename"],
                                content=result.content, assets=assets, warnings=result.warnings)
            serialized = manifest.model_dump_json()
            if len(serialized.encode()) > self.manifest_limit:
                raise ValueError("Manifest exceeds configured byte limit")
            self.db.execute("INSERT INTO generations VALUES (?, ?, ?, ?)",
                            (str(job.generation_id), str(job.deck_id), serialized, str(claim.output_path)))
            self.db.executemany("INSERT INTO assets VALUES (?, ?, ?, ?, ?)",
                               [(i, str(job.deck_id), str(job.generation_id), p, r) for i, p, r in files])
            self.db.execute("UPDATE decks SET active_generation=? WHERE id=?", (str(job.generation_id), str(job.deck_id)))
            self.db.execute("DELETE FROM assets WHERE deck=? AND generation!=?", (str(job.deck_id), str(job.generation_id)))
            self.db.execute("DELETE FROM generations WHERE deck=? AND id!=?", (str(job.deck_id), str(job.generation_id)))
            job.state, job.stage, job.updated_at = "succeeded", "publishing", now()
            job.total_slides = job.completed_slides = result.slide_count
            job.warnings = result.warnings
            job.manifest_url = f"/v1/decks/{job.deck_id}/manifest?generation={job.generation_id}"
            self._save_job(job)
            return True

    def manifest(self, deck_id: UUID, generation: UUID | None = None) -> Manifest:
        with self.lock:
            deck = self._deck(str(deck_id))
            selected = str(generation) if generation else deck["active_generation"]
            row = self.db.execute("SELECT manifest FROM generations WHERE id=? AND deck=?", (selected, str(deck_id))).fetchone()
            if row is None:
                raise ServiceError(404, "not_found", "Complete generation is unavailable.")
            if len(row[0].encode()) > self.manifest_limit:
                raise ServiceError(503, "manifest_unavailable", "Manifest is unavailable.", True)
            manifest = Manifest.model_validate_json(row[0])
            registered = self.db.execute("SELECT record FROM assets WHERE generation=? AND deck=?", (selected, str(deck_id))).fetchall()
            assets = [Asset.model_validate_json(item[0]) for item in registered]
            if {item.asset_id: item for item in assets} != {item.asset_id: item for item in manifest.assets}:
                raise ServiceError(503, "manifest_unavailable", "Stored generation failed asset validation.", True)
            if manifest.deck_id != deck_id or str(manifest.generation_id) != selected:
                raise ServiceError(503, "manifest_unavailable", "Stored generation failed identity validation.", True)
            return manifest

    def asset_bytes(self, identifier: UUID) -> tuple[bytes, Asset]:
        with self.lock:
            row = self.db.execute("""SELECT a.*, g.directory FROM assets a JOIN generations g
                ON a.generation=g.id JOIN decks d ON d.id=a.deck WHERE a.id=? AND d.deleted=0""", (str(identifier),)).fetchone()
            if row is None:
                raise ServiceError(404, "not_found", "Asset is unavailable.")
            path = Path(row["directory"]) / row["relative_path"]
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ServiceError(404, "not_found", "Asset is unavailable.")
            record = Asset.model_validate_json(row["record"])
            if path.stat().st_size != record.byte_length or record.byte_length > 32 * 1024 * 1024:
                raise ServiceError(503, "asset_unavailable", "Stored asset failed validation.", True)
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != record.sha256:
                raise ServiceError(503, "asset_unavailable", "Stored asset failed validation.", True)
            return data, record

    def delete(self, deck_id: UUID) -> Deletion:
        with self.transaction():
            row = self.db.execute("SELECT deleted FROM decks WHERE id=?", (str(deck_id),)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO decks (id, deleted, fence, cleanup) VALUES (?, 1, 1, 'pending')", (str(deck_id),))
            elif not row[0]:
                self.db.execute("""UPDATE decks SET deleted=1, fence=fence+1, revision=NULL, checksum=NULL, filename=NULL,
                    current_job=NULL, active_generation=NULL, cleanup='pending', cleanup_error=NULL WHERE id=?""", (str(deck_id),))
            for table in ("jobs", "requests", "assets", "generations"):
                self.db.execute(f"DELETE FROM {table} WHERE deck=?", (str(deck_id),))
        return self.deletion(deck_id)

    def deletion(self, deck_id: UUID) -> Deletion:
        with self.lock:
            row = self.db.execute("SELECT cleanup, cleanup_error FROM decks WHERE id=? AND deleted=1", (str(deck_id),)).fetchone()
            if row is None:
                raise ServiceError(404, "not_found", "Deletion is unavailable.")
            return Deletion(deck_id=deck_id, state=row[0],
                            error=ErrorDetail.model_validate_json(row[1]) if row[1] else None)

    def cleanup(self) -> None:
        """Only call after the active extractor has stopped, including all its children."""
        with self.transaction():
            for row in self.db.execute("SELECT id FROM decks WHERE deleted=1 AND cleanup='pending'").fetchall():
                directory = self.root / row[0]
                try:
                    if directory.exists():
                        shutil.rmtree(directory)
                    if directory.exists():
                        raise OSError("Cleanup incomplete")
                    self.db.execute("UPDATE decks SET cleanup='complete', cleanup_error=NULL WHERE id=?", (row[0],))
                except OSError:
                    error = ErrorDetail(code="cleanup_failed", message="Cleanup could not remove all files; retry deletion.", retryable=True)
                    self.db.execute("UPDATE decks SET cleanup_error=? WHERE id=?", (error.model_dump_json(), row[0]))
            referenced = {str(Path(r[0]).resolve()) for r in self.db.execute("SELECT directory FROM generations").fetchall()}
            for deck in self.db.execute("SELECT id, revision FROM decks WHERE deleted=0").fetchall():
                directory = self.root / deck[0]
                for source in directory.glob("source-*.pptx"):
                    if source.name != f"source-{deck[1]}.pptx":
                        source.unlink(missing_ok=True)
                for attempt in (directory / "attempts").glob("*"):
                    if str(attempt.resolve()) not in referenced:
                        shutil.rmtree(attempt)

    def recover(self) -> None:
        with self.transaction():
            for row in self.db.execute("SELECT record FROM jobs").fetchall():
                job = Job.model_validate_json(row[0])
                if job.state == "processing":
                    job.state, job.updated_at = "failed", now()
                    job.error = ErrorDetail(code="worker_interrupted", message="Processing was interrupted; retry this job.", retryable=True, stage=job.stage)
                    self._save_job(job)
        self.cleanup()
