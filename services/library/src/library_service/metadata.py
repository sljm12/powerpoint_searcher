from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Literal
from uuid import UUID, uuid4


def sqlite_uri(path: Path, mode: Literal["ro", "rw"]) -> str:
    return f"{path.resolve().as_uri()}?mode={mode}"


class Metadata:
    VERSION = 2

    def __init__(self, root: Path, validate_legacy: Callable[[Metadata], None]) -> None:
        self.root = root
        self.path = root / "metadata.sqlite3"
        self.marker = root / "library.json"
        self.validate_legacy = validate_legacy
        self.db: sqlite3.Connection | None = None
        self.library_id: UUID
        self.database_id: UUID
        try:
            self._open()
        except BaseException:
            self.close()
            raise

    def _open(self) -> None:
        marker_exists, database_exists = self.marker.exists(), self.path.exists()
        if marker_exists:
            marker = self._read_marker()
            if marker["state"] != "active":
                raise RuntimeError("Library initialization is incomplete. Follow the recovery steps in the library README.")
            if not database_exists:
                raise RuntimeError("Library metadata is missing. Restore the coordinated library backup.")
            self.db = sqlite3.connect(sqlite_uri(self.path, "rw"), uri=True, check_same_thread=False)
            self.db.execute("PRAGMA secure_delete=ON")
            self._check_identity(marker)
        elif database_exists:
            self.db = sqlite3.connect(sqlite_uri(self.path, "rw"), uri=True, check_same_thread=False)
            self.db.execute("PRAGMA secure_delete=ON")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version != 1:
                raise RuntimeError("Library metadata has no valid identity marker.")
            self._upgrade_legacy()
        else:
            if any(path.name != "owner.lock" for path in self.root.iterdir()):
                raise RuntimeError("Library metadata is missing from an established data directory.")
            self.library_id, self.database_id = uuid4(), uuid4()
            self._write_marker("preparing")
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.execute("PRAGMA secure_delete=ON")
            self._create_schema()
            self._write_marker("active")

    def _read_marker(self) -> dict[str, str]:
        try:
            value = json.loads(self.marker.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or value.get("version") != 1
                    or value.get("state") not in ("active", "preparing")):
                raise ValueError
            library_id, database_id = UUID(value["library_id"]), UUID(value["database_id"])
            if library_id == database_id:
                raise ValueError
            self.library_id, self.database_id = library_id, database_id
            return {"state": value["state"], "library_id": str(library_id),
                    "database_id": str(database_id)}
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise RuntimeError("Library identity marker is invalid. Restore a coordinated backup.") from None

    def _write_marker(self, state: str) -> None:
        value = json.dumps({"version": 1, "state": state, "library_id": str(self.library_id),
                            "database_id": str(self.database_id)}, sort_keys=True)
        temporary = self.root / "library.json.tmp"
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.marker)

    def _check_identity(self, marker: dict[str, str]) -> None:
        assert self.db is not None
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version != self.VERSION:
            raise RuntimeError("Unsupported library metadata schema.")
        expected_columns = {
            "decks": ("id", "record", "write_order"),
            "operations": ("id", "deck", "state", "due", "record", "write_order"),
            "requests": ("key", "digest", "operation", "write_order"),
            "library_state": ("id", "library_id", "database_id", "transfer_state"),
        }
        for table, expected in expected_columns.items():
            columns = tuple(row[1] for row in self.db.execute(f"PRAGMA table_info({table})"))
            if columns != expected:
                raise RuntimeError("Library metadata schema does not match its version.")
        row = self.db.execute("SELECT library_id, database_id, transfer_state FROM library_state WHERE id=1").fetchone()
        if row != (marker["library_id"], marker["database_id"], "active"):
            raise RuntimeError("Library identity or transfer state does not match its data directory.")

    def _upgrade_legacy(self) -> None:
        assert self.db is not None
        backup = self.root / "metadata.sqlite3.v1-backup"
        if not backup.exists():
            temporary = self.root / "metadata.sqlite3.v1-backup.tmp"
            temporary.unlink(missing_ok=True)
            target = sqlite3.connect(temporary)
            try:
                self.db.backup(target)
            finally:
                target.close()
            os.replace(temporary, backup)
        check = sqlite3.connect(sqlite_uri(backup, "ro"), uri=True)
        try:
            if (check.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or check.execute("PRAGMA user_version").fetchone()[0] != 1):
                raise RuntimeError("Legacy metadata backup failed validation.")
            for table in ("decks", "operations", "requests"):
                source_rows = self.db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                backup_rows = check.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                if source_rows != backup_rows:
                    raise RuntimeError("Legacy metadata backup does not match its source.")
        finally:
            check.close()
        self._validate_legacy_records()
        self.library_id, self.database_id = uuid4(), uuid4()
        self._write_marker("preparing")
        self._create_schema(upgrade=True)
        self.validate_legacy(self)
        self._write_marker("active")

    def _create_schema(self, upgrade: bool = False) -> None:
        assert self.db is not None
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if upgrade:
                for table in ("decks", "operations", "requests"):
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN write_order INTEGER NOT NULL DEFAULT 0")
                    self.db.execute(f"UPDATE {table} SET write_order=rowid")
            else:
                self.db.execute("CREATE TABLE decks (id TEXT PRIMARY KEY, record TEXT NOT NULL, write_order INTEGER NOT NULL)")
                self.db.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, deck TEXT NOT NULL, state TEXT NOT NULL, due REAL NOT NULL, record TEXT NOT NULL, write_order INTEGER NOT NULL)")
                self.db.execute("CREATE TABLE requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, write_order INTEGER NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS library_state (id INTEGER PRIMARY KEY CHECK(id=1), library_id TEXT NOT NULL, database_id TEXT NOT NULL, transfer_state TEXT NOT NULL)")
            self.db.execute("INSERT INTO library_state VALUES (1, ?, ?, 'active')", (str(self.library_id), str(self.database_id)))
            self.db.execute(f"PRAGMA user_version={self.VERSION}")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _validate_legacy_records(self) -> None:
        assert self.db is not None
        try:
            for identifier, raw in self.db.execute("SELECT id, record FROM decks"):
                record = json.loads(raw)
                if not isinstance(record, dict) or record.get("deck_id") != identifier:
                    raise ValueError("Deck record identity does not match its key")
            deck_ids = {row[0] for row in self.db.execute("SELECT id FROM decks")}
            expected_originals: dict[str, tuple[int, str]] = {}
            pending_deletion_ids: set[str] = set()
            for identifier, raw in self.db.execute("SELECT id, record FROM decks"):
                record = json.loads(raw)
                if record.get("checksum") is not None:
                    if record.get("byte_length") is None:
                        raise ValueError("Deck original length is missing")
                    expected_originals[identifier] = (record["byte_length"], record["checksum"])
                elif record.get("state") == "deleting":
                    pending_deletion_ids.add(identifier)
            for identifier, deck, raw in self.db.execute("SELECT id, deck, record FROM operations"):
                record = json.loads(raw)
                if (not isinstance(record, dict) or record.get("operation_id") != identifier
                        or record.get("deck_id") != deck):
                    raise ValueError("Operation identity does not match its indexes")
                if deck not in deck_ids:
                    raise ValueError(f"Operation {identifier} references a missing deck")
            operation_ids = {row[0] for row in self.db.execute("SELECT id FROM operations")}
            for key, operation in self.db.execute("SELECT key, operation FROM requests"):
                if operation not in operation_ids:
                    raise ValueError(f"Request {key} references a missing operation")
            originals = self.root / "originals"
            actual = {path.stem: path for path in originals.glob("*.pptx")} if originals.exists() else {}
            if (not set(expected_originals).issubset(actual)
                    or not set(actual).issubset(set(expected_originals) | pending_deletion_ids)):
                raise ValueError("Legacy originals do not match the metadata records")
            for identifier, (length, checksum) in expected_originals.items():
                path = actual[identifier]
                with path.open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                if path.stat().st_size != length or digest != checksum:
                    raise ValueError("Legacy original failed its integrity check")
            if self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
        except (ValueError, sqlite3.DatabaseError, json.JSONDecodeError):
            raise RuntimeError("Library metadata failed validation. Restore a consistent backup.") from None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        assert self.db is not None
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _next_order(self, table: Literal["decks", "operations", "requests"]) -> int:
        assert self.db is not None
        return int(self.db.execute(f"SELECT COALESCE(MAX(write_order), 0) + 1 FROM {table}").fetchone()[0])

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def deck_record(self, identifier: str) -> str | None:
        assert self.db is not None
        row = self.db.execute("SELECT record FROM decks WHERE id=?", (identifier,)).fetchone()
        return None if row is None else row[0]

    def operation_record(self, identifier: str) -> str | None:
        assert self.db is not None
        row = self.db.execute("SELECT record FROM operations WHERE id=?", (identifier,)).fetchone()
        return None if row is None else row[0]

    def save_deck(self, identifier: str, record: str) -> None:
        assert self.db is not None
        order = self._next_order("decks")
        self.db.execute("INSERT INTO decks (id, record, write_order) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET record=excluded.record, write_order=excluded.write_order", (identifier, record, order))

    def save_operation(self, identifier: str, deck: str, state: str, due: float, record: str) -> None:
        assert self.db is not None
        order = self._next_order("operations")
        self.db.execute("INSERT INTO operations (id, deck, state, due, record, write_order) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET deck=excluded.deck, state=excluded.state, due=excluded.due, record=excluded.record, write_order=excluded.write_order", (identifier, deck, state, due, record, order))

    def save_request(self, key: str, digest: str, operation: str) -> None:
        assert self.db is not None
        order = self._next_order("requests")
        self.db.execute("INSERT INTO requests (key, digest, operation, write_order) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET digest=excluded.digest, operation=excluded.operation, write_order=excluded.write_order", (key, digest, operation, order))

    def request(self, key: str) -> tuple[str, str] | None:
        assert self.db is not None
        row = self.db.execute("SELECT digest, operation FROM requests WHERE key=?", (key,)).fetchone()
        return None if row is None else (row[0], row[1])

    def deck_ids(self, newest_first: bool = False) -> list[str]:
        assert self.db is not None
        order = "DESC" if newest_first else "ASC"
        return [row[0] for row in self.db.execute(f"SELECT id FROM decks ORDER BY write_order {order}")]

    def deck_records(self, newest_first: bool = False) -> list[str]:
        assert self.db is not None
        order = "DESC" if newest_first else "ASC"
        return [row[0] for row in self.db.execute(f"SELECT record FROM decks ORDER BY write_order {order}")]

    def operation_records(self) -> list[str]:
        assert self.db is not None
        return [row[0] for row in self.db.execute("SELECT record FROM operations")]

    def has_deck(self, identifier: str) -> bool:
        assert self.db is not None
        return self.db.execute("SELECT 1 FROM decks WHERE id=?", (identifier,)).fetchone() is not None

    def operation_ids(self, deck: str) -> list[str]:
        assert self.db is not None
        return [row[0] for row in self.db.execute("SELECT id FROM operations WHERE deck=?", (deck,))]

    def due_operation_ids(self, deadline: float) -> list[str]:
        assert self.db is not None
        return [row[0] for row in self.db.execute("SELECT id FROM operations WHERE state IN ('pending', 'processing') AND due<=? ORDER BY due, write_order", (deadline,))]

    def delete_requests(self, deck: str) -> None:
        assert self.db is not None
        self.db.execute("UPDATE requests SET digest='deleted' WHERE operation IN (SELECT id FROM operations WHERE deck=?)", (deck,))
