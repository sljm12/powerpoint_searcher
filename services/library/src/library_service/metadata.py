from __future__ import annotations

import hashlib
import importlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Literal
from uuid import UUID, uuid4


def sqlite_uri(path: Path, mode: Literal["ro", "rw"]) -> str:
    return f"{path.resolve().as_uri()}?mode={mode}"


class StorageUnavailable(RuntimeError):
    pass


class Metadata:
    VERSION = 2
    LOCK_KEY = 7_312_850_019_711
    SCHEMA = "powerpoint_library"

    def __init__(self, root: Path, validate_legacy: Callable[[Metadata], None],
                 backend: Literal["sqlite", "postgresql"] = "sqlite", dsn: str | None = None,
                 allow_transfer: bool = False) -> None:
        self.root = root
        self.path = root / "metadata.sqlite3"
        self.marker = root / "library.json"
        self.validate_legacy = validate_legacy
        self.db: sqlite3.Connection | None = None
        self.pg: Any = None
        self.backend = backend
        self.dsn = dsn
        self.allow_transfer = allow_transfer
        self.failed = False
        self.library_id: UUID
        self.database_id: UUID
        try:
            journal = self.root / "library-transfer.json"
            if journal.exists() and not allow_transfer:
                transfer = json.loads(journal.read_text(encoding="utf-8"))
                if transfer.get("phase") != "complete":
                    raise RuntimeError("Library transfer is incomplete. Resume it with library_service.migrate.")
            if backend == "sqlite":
                self._open()
            else:
                self._open_postgresql()
        except BaseException:
            self.close()
            raise

    def _open(self) -> None:
        marker_exists, database_exists = self.marker.exists(), self.path.exists()
        if marker_exists:
            marker = self._read_marker()
            if marker.get("backend", "sqlite") != "sqlite":
                raise RuntimeError("Library marker selects a different database backend.")
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
            return {"state": value["state"], "backend": value.get("backend", "sqlite"),
                    "library_id": str(library_id), "root_binding": value.get("root_binding", ""),
                    "database_id": str(database_id)}
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise RuntimeError("Library identity marker is invalid. Restore a coordinated backup.") from None

    def _write_marker(self, state: str) -> None:
        value = json.dumps({"version": 1, "state": state, "backend": self.backend,
                            "library_id": str(self.library_id),
                            "database_id": str(self.database_id)}, sort_keys=True)
        if self.backend == "postgresql":
            data = json.loads(value)
            data["root_binding"] = self._root_binding()
            value = json.dumps(data, sort_keys=True)
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
        allowed_states = ("active", "sealed") if self.allow_transfer else ("active",)
        if row is None or row[:2] != (marker["library_id"], marker["database_id"]) or row[2] not in allowed_states:
            raise RuntimeError("Library identity or transfer state does not match its data directory.")

    def _open_postgresql(self) -> None:
        if not self.dsn:
            raise ValueError("LIBRARY_POSTGRES_DSN is required for PostgreSQL.")
        psycopg = importlib.import_module("psycopg")

        marker_exists = self.marker.exists()
        marker: dict[str, str] | None = self._read_marker() if marker_exists else None
        if marker is not None and marker.get("backend", "sqlite") != "postgresql":
            raise RuntimeError("Library marker selects a different database backend.")
        if marker is not None and marker.get("root_binding") != self._root_binding():
            raise RuntimeError("PostgreSQL metadata is bound to a different library root.")
        if marker is None and any(path.name != "owner.lock" for path in self.root.iterdir()):
            raise RuntimeError("Library metadata is missing from an established data directory.")
        try:
            self.pg = psycopg.connect(
                self.dsn, autocommit=True, connect_timeout=5, keepalives=1,
                keepalives_idle=5, keepalives_interval=1, keepalives_count=3,
                tcp_user_timeout=5000,
                options="-c statement_timeout=5000 -c lock_timeout=1000",
            )
            if not self.pg.execute("SELECT pg_try_advisory_lock(%s)", (self.LOCK_KEY,)).fetchone()[0]:
                raise RuntimeError("Library database already has an owner.")
            unrelated = self.pg.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_type='BASE TABLE' "
                "AND table_schema NOT IN ('pg_catalog', 'information_schema', %s) LIMIT 1",
                (self.SCHEMA,),
            ).fetchone()
            if unrelated is not None:
                raise RuntimeError("Configure a dedicated PostgreSQL database for library metadata.")
            database_name = self.pg.execute("SELECT current_database()").fetchone()[0]
            if database_name.lower() in ("postgres", "template0", "template1"):
                raise RuntimeError("Configure a dedicated PostgreSQL database for library metadata.")
            state_table = self.pg.execute(
                "SELECT to_regclass('powerpoint_library.library_state')"
            ).fetchone()[0]
            if marker is not None:
                if state_table is None:
                    raise RuntimeError("Library metadata is missing. Restore the coordinated library backup.")
                self.pg.execute("SET search_path TO powerpoint_library")
                self.library_id, self.database_id = UUID(marker["library_id"]), UUID(marker["database_id"])
                self._check_identity_postgresql(marker)
                return
            if self.pg.execute("SELECT to_regnamespace('powerpoint_library')").fetchone()[0] is not None:
                raise RuntimeError("PostgreSQL database already contains library metadata without its root marker.")
            self.pg.execute("CREATE SCHEMA powerpoint_library")
            self.pg.execute("SET search_path TO powerpoint_library")
            self.library_id, self.database_id = uuid4(), uuid4()
            self._write_marker("preparing")
            self._create_schema_postgresql()
            self._write_marker("active")
        except BaseException:
            self.close()
            raise

    def _create_schema_postgresql(self) -> None:
        assert self.pg is not None
        with self.pg.transaction():
            self.pg.execute("CREATE TABLE decks (id TEXT PRIMARY KEY, record TEXT NOT NULL, write_order BIGINT NOT NULL)")
            self.pg.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, deck TEXT NOT NULL, state TEXT NOT NULL, due DOUBLE PRECISION NOT NULL, record TEXT NOT NULL, write_order BIGINT NOT NULL)")
            self.pg.execute("CREATE TABLE requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, write_order BIGINT NOT NULL)")
            self.pg.execute("CREATE TABLE library_state (id SMALLINT PRIMARY KEY CHECK (id=1), library_id TEXT NOT NULL, database_id TEXT NOT NULL, transfer_state TEXT NOT NULL, root_binding TEXT NOT NULL)")
            self.pg.execute("INSERT INTO library_state VALUES (1, %s, %s, 'active', %s)",
                             (str(self.library_id), str(self.database_id), self._root_binding()))
            self.pg.execute("CREATE TABLE schema_version (id SMALLINT PRIMARY KEY CHECK (id=1), version INTEGER NOT NULL)")
            self.pg.execute("INSERT INTO schema_version VALUES (1, %s)", (self.VERSION,))

    def _check_identity_postgresql(self, marker: dict[str, str]) -> None:
        assert self.pg is not None
        version = self.pg.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        expected = {
            "decks": ("id", "record", "write_order"),
            "operations": ("id", "deck", "state", "due", "record", "write_order"),
            "requests": ("key", "digest", "operation", "write_order"),
            "library_state": ("id", "library_id", "database_id", "transfer_state", "root_binding"),
        }
        for table, columns in expected.items():
            actual = tuple(row[0] for row in self.pg.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                (self.SCHEMA, table),
            ))
            if actual != columns:
                raise RuntimeError("Library metadata schema does not match its version.")
        row = self.pg.execute("SELECT library_id, database_id, transfer_state, root_binding FROM library_state WHERE id=1").fetchone()
        allowed_states = ("active", "sealed") if self.allow_transfer else ("active",)
        if (version != (self.VERSION,) or row is None
                or row[:2] != (marker["library_id"], marker["database_id"])
                or row[2] not in allowed_states or row[3] != self._root_binding()):
            raise RuntimeError("Library identity or transfer state does not match its data directory.")

    def _root_binding(self) -> str:
        return hashlib.sha256(os.path.normcase(str(self.root.resolve())).encode("utf-8")).hexdigest()

    def _execute(self, sqlite_sql: str, postgres_sql: str | None = None,
                 params: tuple[Any, ...] = ()) -> Any:
        try:
            if self.failed:
                raise StorageUnavailable("Library storage is unavailable; restart the service.")
            if self.backend == "postgresql":
                assert self.pg is not None
                return self.pg.execute(postgres_sql or sqlite_sql, params)
            assert self.db is not None
            return self.db.execute(sqlite_sql, params)
        except StorageUnavailable:
            raise
        except Exception as error:
            if self._is_storage_error(error):
                self.failed = True
                raise StorageUnavailable("Library storage is unavailable; restart the service.") from None
            raise

    @staticmethod
    def _is_storage_error(error: Exception) -> bool:
        return isinstance(error, sqlite3.DatabaseError) or type(error).__module__.startswith("psycopg")

    def check_health(self) -> bool:
        if self.failed:
            return False
        try:
            self._execute("SELECT 1")
        except StorageUnavailable:
            return False
        return True

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
        if self.backend == "postgresql":
            assert self.pg is not None
            try:
                if self.failed:
                    raise StorageUnavailable("Library storage is unavailable; restart the service.")
                with self.pg.transaction():
                    yield
            except StorageUnavailable:
                raise
            except Exception as error:
                if self._is_storage_error(error):
                    self.failed = True
                    raise StorageUnavailable("Library storage is unavailable; restart the service.") from None
                raise
            return
        assert self.db is not None
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _next_order(self, table: Literal["decks", "operations", "requests"]) -> int:
        return int(self._execute(f"SELECT COALESCE(MAX(write_order), 0) + 1 FROM {table}").fetchone()[0])

    def close(self) -> None:
        if self.pg is not None:
            self.pg.close()
            self.pg = None
        if self.db is not None:
            self.db.close()
            self.db = None

    def deck_record(self, identifier: str) -> str | None:
        row = self._execute("SELECT record FROM decks WHERE id=?", "SELECT record FROM decks WHERE id=%s", (identifier,)).fetchone()
        return None if row is None else row[0]

    def operation_record(self, identifier: str) -> str | None:
        row = self._execute("SELECT record FROM operations WHERE id=?", "SELECT record FROM operations WHERE id=%s", (identifier,)).fetchone()
        return None if row is None else row[0]

    def save_deck(self, identifier: str, record: str) -> None:
        order = self._next_order("decks")
        self._execute("INSERT INTO decks (id, record, write_order) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET record=excluded.record, write_order=excluded.write_order", "INSERT INTO decks (id, record, write_order) VALUES (%s, %s, %s) ON CONFLICT(id) DO UPDATE SET record=EXCLUDED.record, write_order=EXCLUDED.write_order", (identifier, record, order))

    def save_operation(self, identifier: str, deck: str, state: str, due: float, record: str) -> None:
        order = self._next_order("operations")
        self._execute("INSERT INTO operations (id, deck, state, due, record, write_order) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET deck=excluded.deck, state=excluded.state, due=excluded.due, record=excluded.record, write_order=excluded.write_order", "INSERT INTO operations (id, deck, state, due, record, write_order) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT(id) DO UPDATE SET deck=EXCLUDED.deck, state=EXCLUDED.state, due=EXCLUDED.due, record=EXCLUDED.record, write_order=EXCLUDED.write_order", (identifier, deck, state, due, record, order))

    def save_request(self, key: str, digest: str, operation: str) -> None:
        order = self._next_order("requests")
        self._execute("INSERT INTO requests (key, digest, operation, write_order) VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET digest=excluded.digest, operation=excluded.operation, write_order=excluded.write_order", "INSERT INTO requests (key, digest, operation, write_order) VALUES (%s, %s, %s, %s) ON CONFLICT(key) DO UPDATE SET digest=EXCLUDED.digest, operation=EXCLUDED.operation, write_order=EXCLUDED.write_order", (key, digest, operation, order))

    def request(self, key: str) -> tuple[str, str] | None:
        row = self._execute("SELECT digest, operation FROM requests WHERE key=?", "SELECT digest, operation FROM requests WHERE key=%s", (key,)).fetchone()
        return None if row is None else (row[0], row[1])

    def deck_ids(self, newest_first: bool = False) -> list[str]:
        order = "DESC" if newest_first else "ASC"
        return [row[0] for row in self._execute(f"SELECT id FROM decks ORDER BY write_order {order}")]

    def deck_records(self, newest_first: bool = False) -> list[str]:
        order = "DESC" if newest_first else "ASC"
        return [row[0] for row in self._execute(f"SELECT record FROM decks ORDER BY write_order {order}")]

    def operation_records(self) -> list[str]:
        return [row[0] for row in self._execute("SELECT record FROM operations ORDER BY write_order")]

    def has_deck(self, identifier: str) -> bool:
        return self._execute("SELECT 1 FROM decks WHERE id=?", "SELECT 1 FROM decks WHERE id=%s", (identifier,)).fetchone() is not None

    def operation_ids(self, deck: str) -> list[str]:
        return [row[0] for row in self._execute("SELECT id FROM operations WHERE deck=?", "SELECT id FROM operations WHERE deck=%s", (deck,))]

    def due_operation_ids(self, deadline: float) -> list[str]:
        return [row[0] for row in self._execute("SELECT id FROM operations WHERE state IN ('pending', 'processing') AND due<=? ORDER BY due, write_order", "SELECT id FROM operations WHERE state IN ('pending', 'processing') AND due<=%s ORDER BY due, write_order", (deadline,))]

    def delete_requests(self, deck: str) -> None:
        self._execute("UPDATE requests SET digest='deleted' WHERE operation IN (SELECT id FROM operations WHERE deck=?)", "UPDATE requests SET digest='deleted' WHERE operation IN (SELECT id FROM operations WHERE deck=%s)", (deck,))

    def transfer_snapshot(self) -> dict[str, list[tuple[Any, ...]]]:
        return {
            "decks": [tuple(row) for row in self._execute("SELECT id, record, write_order FROM decks ORDER BY write_order")],
            "operations": [tuple(row) for row in self._execute("SELECT id, deck, state, due, record, write_order FROM operations ORDER BY write_order")],
            "requests": [tuple(row) for row in self._execute("SELECT key, digest, operation, write_order FROM requests ORDER BY write_order")],
        }

    def set_transfer_state(self, state: Literal["active", "inactive", "sealed"]) -> None:
        with self.transaction():
            self._execute("UPDATE library_state SET transfer_state=? WHERE id=1",
                          "UPDATE library_state SET transfer_state=%s WHERE id=1", (state,))
