from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from .api import private_directory
from .metadata import Metadata
from .store import Store, StoredDeck, StoredOperation

Backend = Literal["sqlite", "postgresql"]
TABLES = ("decks", "operations", "requests")


def _psycopg() -> Any:
    return importlib.import_module("psycopg")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _lock_root(root: Path) -> Any:
    handle = (root / "owner.lock").open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("Stop the library service before transferring its database.") from None
    return handle


def _unlock_root(handle: Any) -> None:
    if sys.platform == "win32":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    handle.close()


def _digest(snapshot: dict[str, list[tuple[Any, ...]]]) -> str:
    canonical = {name: [list(row) for row in rows] for name, rows in snapshot.items()}
    for row in canonical["decks"]:
        row[1] = json.loads(row[1])
    for row in canonical["operations"]:
        row[4] = json.loads(row[4])
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_snapshot(root: Path, snapshot: dict[str, list[tuple[Any, ...]]]) -> None:
    decks: dict[str, StoredDeck] = {}
    for identifier, raw, _order in snapshot["decks"]:
        deck = StoredDeck.model_validate_json(raw)
        if str(deck.deck_id) != identifier:
            raise RuntimeError("A deck record does not match its database key.")
        decks[identifier] = deck
        if deck.checksum is not None:
            original = root / "originals" / f"{identifier}.pptx"
            if not original.is_file():
                raise RuntimeError("A recorded original is missing. Restore a coordinated backup.")
            with original.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if original.stat().st_size != deck.byte_length or digest != deck.checksum:
                raise RuntimeError("A recorded original failed its integrity check.")
    operations: set[str] = set()
    for identifier, deck_id, _state, _due, raw, _order in snapshot["operations"]:
        operation = StoredOperation.model_validate_json(raw)
        if (str(operation.operation_id) != identifier or str(operation.deck_id) != deck_id
                or deck_id not in decks):
            raise RuntimeError("An operation record has an invalid deck or key reference.")
        operations.add(identifier)
    for _key, _digest_value, operation, _order in snapshot["requests"]:
        if operation not in operations:
            raise RuntimeError("A request key refers to a missing operation.")


def _rows(connection: Any) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "decks": [tuple(row) for row in connection.execute("SELECT id, record, write_order FROM decks ORDER BY write_order")],
        "operations": [tuple(row) for row in connection.execute("SELECT id, deck, state, due, record, write_order FROM operations ORDER BY write_order")],
        "requests": [tuple(row) for row in connection.execute("SELECT key, digest, operation, write_order FROM requests ORDER BY write_order")],
    }


def _state(connection: Any, backend: Backend) -> tuple[str, str, str] | None:
    if backend == "sqlite":
        if not (connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_state'").fetchone()):
            return None
    else:
        if not connection.execute("SELECT to_regclass('powerpoint_library.library_state')").fetchone()[0]:
            return None
    row = connection.execute("SELECT library_id, database_id, transfer_state FROM library_state WHERE id=1").fetchone()
    return None if row is None else tuple(row)


def _root_binding(root: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()


def _validate_journal(root: Path, journal: Any) -> None:
    phases = {"created", "target_copied", "source_sealed", "target_active", "marker_published", "complete"}
    if (not isinstance(journal, dict) or journal.get("version") != 1
            or journal.get("phase") not in phases
            or journal.get("source_backend") not in ("sqlite", "postgresql")
            or journal.get("target_backend") not in ("sqlite", "postgresql")):
        raise RuntimeError("Transfer journal is invalid. Preserve it and restore a coordinated backup.")
    for identity in ("transfer_id", "source_library_id", "source_database_id", "target_database_id"):
        try:
            UUID(journal[identity])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Transfer journal is invalid. Preserve it and restore a coordinated backup.") from None
    archive_name = journal.get("sqlite_archive")
    if archive_name is not None:
        try:
            UUID(archive_name.removeprefix("metadata.sqlite3.inactive."))
            archive_path = (root / archive_name).resolve()
        except (AttributeError, OSError, ValueError):
            raise RuntimeError("Transfer journal archive path is invalid.") from None
        if (archive_path.parent != root
                or not archive_name.startswith("metadata.sqlite3.inactive.")):
            raise RuntimeError("Transfer journal archive path escapes the private library root.")


def _create_target(root: Path, backend: Backend, dsn: str | None,
                   library_id: UUID, database_id: UUID) -> tuple[Any, str]:
    if backend == "sqlite":
        path = root / "metadata.sqlite3"
        connection: Any = sqlite3.connect(path)
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE decks (id TEXT PRIMARY KEY, record TEXT NOT NULL, write_order INTEGER NOT NULL)")
        connection.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, deck TEXT NOT NULL, state TEXT NOT NULL, due REAL NOT NULL, record TEXT NOT NULL, write_order INTEGER NOT NULL)")
        connection.execute("CREATE TABLE requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, write_order INTEGER NOT NULL)")
        connection.execute("CREATE TABLE library_state (id INTEGER PRIMARY KEY CHECK(id=1), library_id TEXT NOT NULL, database_id TEXT NOT NULL, transfer_state TEXT NOT NULL)")
        connection.execute("INSERT INTO library_state VALUES (1, ?, ?, 'inactive')", (str(library_id), str(database_id)))
        connection.execute(f"PRAGMA user_version={Metadata.VERSION}")
        connection.commit()
        return connection, str(path)
    psycopg = _psycopg()

    if not dsn:
        raise ValueError("LIBRARY_POSTGRES_DSN is required for PostgreSQL.")
    connection = psycopg.connect(
        dsn, autocommit=True, connect_timeout=5, keepalives=1, keepalives_idle=5,
        keepalives_interval=1, keepalives_count=3, tcp_user_timeout=5000,
        options="-c statement_timeout=5000 -c lock_timeout=1000",
    )
    lock = connection.execute("SELECT pg_try_advisory_lock(%s)", (Metadata.LOCK_KEY,)).fetchone()
    if lock is None or not lock[0]:
        connection.close()
        raise RuntimeError("Library database already has an owner.")
    unrelated = connection.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_type='BASE TABLE' "
        "AND table_schema NOT IN ('pg_catalog', 'information_schema', 'powerpoint_library') LIMIT 1"
    ).fetchone()
    if unrelated is not None:
        connection.close()
        raise RuntimeError("Configure a dedicated PostgreSQL database for library metadata.")
    if connection.execute("SELECT to_regnamespace('powerpoint_library')").fetchone()[0] is not None:
        connection.close()
        raise RuntimeError("PostgreSQL target already contains the library schema.")
    connection.execute("CREATE SCHEMA powerpoint_library")
    connection.execute("SET search_path TO powerpoint_library")
    with connection.transaction():
        connection.execute("CREATE TABLE decks (id TEXT PRIMARY KEY, record TEXT NOT NULL, write_order BIGINT NOT NULL)")
        connection.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, deck TEXT NOT NULL, state TEXT NOT NULL, due DOUBLE PRECISION NOT NULL, record TEXT NOT NULL, write_order BIGINT NOT NULL)")
        connection.execute("CREATE TABLE requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, write_order BIGINT NOT NULL)")
        connection.execute("CREATE TABLE library_state (id SMALLINT PRIMARY KEY CHECK(id=1), library_id TEXT NOT NULL, database_id TEXT NOT NULL, transfer_state TEXT NOT NULL, root_binding TEXT NOT NULL)")
        connection.execute("INSERT INTO library_state VALUES (1, %s, %s, 'inactive', %s)",
                           (str(library_id), str(database_id), _root_binding(root)))
        connection.execute("CREATE TABLE schema_version (id SMALLINT PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (1, %s)", (Metadata.VERSION,))
    return connection, "postgresql:powerpoint_library"


def _open_existing_target(root: Path, backend: Backend, dsn: str | None) -> Any:
    if backend == "sqlite":
        return sqlite3.connect(root / "metadata.sqlite3")
    psycopg = _psycopg()

    connection = psycopg.connect(
        dsn or "", autocommit=True, connect_timeout=5, keepalives=1,
        keepalives_idle=5, keepalives_interval=1, keepalives_count=3, tcp_user_timeout=5000,
        options="-c statement_timeout=5000 -c lock_timeout=1000",
    )
    lock = connection.execute("SELECT pg_try_advisory_lock(%s)", (Metadata.LOCK_KEY,)).fetchone()
    if lock is None or not lock[0]:
        connection.close()
        raise RuntimeError("Library database already has an owner.")
    connection.execute("SET search_path TO powerpoint_library")
    return connection


def _preflight_postgresql_target(dsn: str) -> None:
    psycopg = _psycopg()

    connection = psycopg.connect(
        dsn, autocommit=True, connect_timeout=5, keepalives=1,
        keepalives_idle=5, keepalives_interval=1, keepalives_count=3,
        tcp_user_timeout=5000, options="-c statement_timeout=5000 -c lock_timeout=1000",
    )
    try:
        lock = connection.execute("SELECT pg_try_advisory_lock(%s)", (Metadata.LOCK_KEY,)).fetchone()
        if lock is None or not lock[0]:
            raise RuntimeError("Library database already has an owner.")
        unrelated = connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_type='BASE TABLE' "
            "AND table_schema NOT IN ('pg_catalog', 'information_schema', 'powerpoint_library') LIMIT 1"
        ).fetchone()
        if unrelated is not None:
            raise RuntimeError("Configure a dedicated PostgreSQL database for library metadata.")
        database = connection.execute("SELECT current_database()").fetchone()
        if database is None or database[0].lower() in ("postgres", "template0", "template1"):
            raise RuntimeError("Configure a dedicated PostgreSQL database for library metadata.")
        schema = connection.execute("SELECT to_regnamespace('powerpoint_library')").fetchone()
        if schema is None or schema[0] is not None:
            raise RuntimeError("PostgreSQL target already contains library metadata.")
    finally:
        connection.close()


def _initialize_existing_target(connection: Any, backend: Backend,
                                root: Path, library_id: UUID, database_id: UUID) -> None:
    if backend == "sqlite":
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    else:
        tables = connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='powerpoint_library'").fetchall()
    if tables:
        raise RuntimeError("An incomplete target contains unexpected tables; preserve it for recovery.")
    if backend == "sqlite":
        with connection:
            connection.execute("CREATE TABLE decks (id TEXT PRIMARY KEY, record TEXT NOT NULL, write_order INTEGER NOT NULL)")
            connection.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, deck TEXT NOT NULL, state TEXT NOT NULL, due REAL NOT NULL, record TEXT NOT NULL, write_order INTEGER NOT NULL)")
            connection.execute("CREATE TABLE requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, write_order INTEGER NOT NULL)")
            connection.execute("CREATE TABLE library_state (id INTEGER PRIMARY KEY CHECK(id=1), library_id TEXT NOT NULL, database_id TEXT NOT NULL, transfer_state TEXT NOT NULL)")
            connection.execute("INSERT INTO library_state VALUES (1, ?, ?, 'inactive')", (str(library_id), str(database_id)))
            connection.execute(f"PRAGMA user_version={Metadata.VERSION}")
        return
    with connection.transaction():
        connection.execute("CREATE TABLE decks (id TEXT PRIMARY KEY, record TEXT NOT NULL, write_order BIGINT NOT NULL)")
        connection.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, deck TEXT NOT NULL, state TEXT NOT NULL, due DOUBLE PRECISION NOT NULL, record TEXT NOT NULL, write_order BIGINT NOT NULL)")
        connection.execute("CREATE TABLE requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, write_order BIGINT NOT NULL)")
        connection.execute("CREATE TABLE library_state (id SMALLINT PRIMARY KEY CHECK(id=1), library_id TEXT NOT NULL, database_id TEXT NOT NULL, transfer_state TEXT NOT NULL, root_binding TEXT NOT NULL)")
        connection.execute("INSERT INTO library_state VALUES (1, %s, %s, 'inactive', %s)",
                           (str(library_id), str(database_id), _root_binding(root)))
        connection.execute("CREATE TABLE schema_version (id SMALLINT PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (1, %s)", (Metadata.VERSION,))


def _write_target(connection: Any, backend: Backend,
                  snapshot: dict[str, list[tuple[Any, ...]]]) -> None:
    placeholders = ("?", "%s")[backend == "postgresql"]
    with connection if backend == "sqlite" else connection.transaction():
        for row in snapshot["decks"]:
            connection.execute(f"INSERT INTO decks VALUES ({placeholders}, {placeholders}, {placeholders})", row)
        for row in snapshot["operations"]:
            connection.execute(f"INSERT INTO operations VALUES ({', '.join([placeholders] * 6)})", row)
        for row in snapshot["requests"]:
            connection.execute(f"INSERT INTO requests VALUES ({', '.join([placeholders] * 4)})", row)


def _publish_marker(root: Path, backend: Backend, library_id: str, database_id: str) -> None:
    value = {"version": 1, "state": "active", "backend": backend,
             "library_id": library_id, "database_id": database_id}
    if backend == "postgresql":
        value["root_binding"] = _root_binding(root)
    _write_json(root / "library.json", value)


def migrate(root: Path, target_backend: Backend, dsn: str | None) -> dict[str, int]:
    root = root.resolve()
    private_directory(root)
    lock = _lock_root(root)
    source: Metadata | None = None
    target_connection: Any = None
    try:
        marker_path, journal_path = root / "library.json", root / "library-transfer.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        source_backend: Backend = marker.get("backend", "sqlite")
        if source_backend not in ("sqlite", "postgresql"):
            raise RuntimeError("Library marker selects an unsupported database backend.")
        if journal_path.exists():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            _validate_journal(root, journal)
            if journal.get("phase") == "complete":
                if (marker.get("backend") == target_backend
                        and marker.get("database_id") == journal.get("target_database_id")):
                    return cast(dict[str, int], journal.get("counts", {}))
                archive = root / f"library-transfer-{journal['transfer_id']}.json"
                if archive.exists():
                    raise RuntimeError("A previous transfer journal already exists.")
                os.replace(journal_path, archive)
            elif (marker.get("backend") == target_backend
                  and marker.get("database_id") == journal.get("target_database_id")):
                journal["phase"] = "complete"
                _write_json(journal_path, journal)
                return cast(dict[str, int], journal.get("counts", {}))
            elif journal.get("target_backend") != target_backend:
                raise RuntimeError("An incomplete transfer targets a different backend; resume it first.")
        if source_backend == target_backend:
            raise RuntimeError("Source and target database backends are the same.")
        if target_backend == "postgresql" and (not journal_path.exists()):
            if not dsn:
                raise ValueError("LIBRARY_POSTGRES_DSN is required for PostgreSQL transfer.")
            _preflight_postgresql_target(dsn)
        if not journal_path.exists():
            transfer_id = str(uuid4())
            prior_sqlite = root / "metadata.sqlite3"
            archive_name = None
            if target_backend == "sqlite" and prior_sqlite.exists():
                try:
                    check = sqlite3.connect(prior_sqlite.resolve().as_uri() + "?mode=ro", uri=True)
                    try:
                        prior_state = check.execute(
                            "SELECT library_id, database_id, transfer_state FROM library_state WHERE id=1"
                        ).fetchone()
                    finally:
                        check.close()
                except sqlite3.DatabaseError:
                    raise RuntimeError("Existing SQLite snapshot is not a valid inactive library database.") from None
                if (prior_state is None or prior_state[0] != marker["library_id"]
                        or prior_state[2] != "sealed"):
                    raise RuntimeError("Existing SQLite database is not a matching inactive snapshot.")
                try:
                    prior_database_id = UUID(prior_state[1])
                except (ValueError, TypeError):
                    raise RuntimeError("Existing SQLite snapshot has an invalid database identity.") from None
                archive_name = f"metadata.sqlite3.inactive.{prior_database_id}"
                archive_path = root / archive_name
                if archive_path.resolve().parent != root:
                    raise RuntimeError("SQLite archive path escapes the private library root.")
                if archive_path.exists():
                    raise RuntimeError("The inactive SQLite snapshot archive already exists.")
            journal = {"version": 1, "transfer_id": transfer_id, "phase": "created",
                       "source_backend": source_backend, "source_library_id": marker["library_id"],
                       "source_database_id": marker["database_id"], "target_backend": target_backend,
                       "target_database_id": str(uuid4()), "sqlite_archive": archive_name}
            _write_json(journal_path, journal)
        _validate_journal(root, journal)

        if source_backend == "postgresql" or target_backend == "postgresql":
            if not dsn:
                raise ValueError("LIBRARY_POSTGRES_DSN is required for PostgreSQL transfer.")
        source = Metadata(root, Store._validate_legacy_records, source_backend, dsn, allow_transfer=True)
        if source.library_id != UUID(journal["source_library_id"]):
            raise RuntimeError("Transfer journal library identity does not match its source.")
        snapshot = source.transfer_snapshot()
        _validate_snapshot(root, snapshot)
        digest = _digest(snapshot)
        backup = root / f"metadata-transfer-{journal['transfer_id']}.json"
        if not backup.exists():
            _write_json(backup, {"library_id": str(source.library_id), "database_id": str(source.database_id),
                                 "snapshot": snapshot, "sha256": digest})
        elif json.loads(backup.read_text(encoding="utf-8")).get("sha256") != digest:
            raise RuntimeError("Source metadata changed after the transfer backup was written.")

        if target_backend == "sqlite" and journal.get("sqlite_archive"):
            source_path = root / "metadata.sqlite3"
            archive = root / journal["sqlite_archive"]
            if source_path.exists() and not archive.exists():
                if source_path.resolve().parent != root:
                    raise RuntimeError("SQLite archive path escapes the private library root.")
                os.replace(source_path, archive)

        try:
            if journal["phase"] == "created":
                target_name = str(root / "metadata.sqlite3")
                try:
                    target_connection, target_name = _create_target(
                        root, target_backend, dsn, source.library_id, UUID(journal["target_database_id"]))
                    _write_target(target_connection, target_backend, snapshot)
                except Exception:
                    if target_connection is not None:
                        target_connection.close()
                    target_connection = _open_existing_target(root, target_backend, dsn)
                    target_state = _state(target_connection, target_backend)
                    target_snapshot = _rows(target_connection) if target_state is not None else {}
                    if target_state is None:
                        _initialize_existing_target(target_connection, target_backend, root, source.library_id,
                                                    UUID(journal["target_database_id"]))
                        target_snapshot = {}
                    elif (target_state[:2] != (str(source.library_id), journal["target_database_id"])
                          or target_state[2] not in ("inactive", "active")
                          or (target_snapshot and _digest(target_snapshot) != digest)):
                        raise RuntimeError("Existing transfer target does not match the verified journal.") from None
                    if not target_snapshot:
                        _write_target(target_connection, target_backend, snapshot)
                target_snapshot = _rows(target_connection)
                if _digest(target_snapshot) != digest:
                    raise RuntimeError("Transfer target failed record verification.")
                journal = {**journal, "phase": "target_copied", "snapshot_sha256": digest,
                           "counts": {name: len(snapshot[name]) for name in TABLES},
                           "target": target_name}
                _write_json(journal_path, journal)
            else:
                if target_backend == "sqlite":
                    target_connection = sqlite3.connect(root / "metadata.sqlite3")
                else:
                    psycopg = _psycopg()
                    target_connection = psycopg.connect(dsn or "", autocommit=True, connect_timeout=5,
                        keepalives=1, keepalives_idle=5, keepalives_interval=1, keepalives_count=3,
                        tcp_user_timeout=5000,
                        options="-c statement_timeout=5000 -c lock_timeout=1000")
                    if not target_connection.execute("SELECT pg_try_advisory_lock(%s)", (Metadata.LOCK_KEY,)).fetchone()[0]:
                        raise RuntimeError("Library database already has an owner.")
                    target_connection.execute("SET search_path TO powerpoint_library")
                target_state = _state(target_connection, target_backend)
                if (target_state is None or target_state[:2] != (str(source.library_id), journal["target_database_id"])
                        or target_state[2] not in ("inactive", "active")
                        or _digest(_rows(target_connection)) != digest):
                    raise RuntimeError("Existing transfer target does not match the verified journal.")
                if journal["phase"] == "created":
                    journal = {**journal, "phase": "target_copied", "snapshot_sha256": digest,
                               "counts": {name: len(snapshot[name]) for name in TABLES}}
                    _write_json(journal_path, journal)

            assert journal["phase"] in ("target_copied", "source_sealed", "target_active", "marker_published")
            if journal["phase"] == "target_copied":
                source.set_transfer_state("sealed")
                journal = {**journal, "phase": "source_sealed"}
                _write_json(journal_path, journal)
            if journal["phase"] == "source_sealed":
                if target_backend == "sqlite":
                    with target_connection:
                        target_connection.execute("UPDATE library_state SET transfer_state='active' WHERE id=1")
                else:
                    target_connection.execute("UPDATE library_state SET transfer_state='active' WHERE id=1")
                journal = {**journal, "phase": "target_active"}
                _write_json(journal_path, journal)
            if journal["phase"] == "target_active":
                _publish_marker(root, target_backend, str(source.library_id), journal["target_database_id"])
                journal = {**journal, "phase": "marker_published"}
                _write_json(journal_path, journal)
            journal = {**journal, "phase": "complete"}
            _write_json(journal_path, journal)
            return cast(dict[str, int], journal["counts"])
        finally:
            if target_connection is not None:
                target_connection.close()
    finally:
        if source is not None:
            source.close()
        _unlock_root(lock)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transfer library metadata between SQLite and PostgreSQL while services are stopped.")
    parser.add_argument("--target-backend", choices=("sqlite", "postgresql"), required=True)
    parser.add_argument("--confirm-extraction-stopped", action="store_true",
                        help="Confirm the extraction service is stopped and the frontend disconnected.")
    args = parser.parse_args()
    if not args.confirm_extraction_stopped:
        parser.error("Stop the extraction service and disconnect the frontend, then pass --confirm-extraction-stopped")
    root = os.environ.get("LIBRARY_DATA_DIR")
    if not root:
        parser.error("Set LIBRARY_DATA_DIR")
    try:
        counts = migrate(Path(root), args.target_backend, os.environ.get("LIBRARY_POSTGRES_DSN"))
    except Exception as error:
        message = "PostgreSQL operation failed." if type(error).__module__.startswith("psycopg") else "Transfer failed. Check the library storage and follow the recovery instructions."
        print(message, file=sys.stderr)
        return 1
    print(json.dumps({"status": "complete", "counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
