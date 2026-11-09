from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from library_service.store import Store


def upload(store: Store, root: Path, filename: str, data: bytes):
    source = root / f"{uuid4()}.pptx"
    source.write_bytes(data)
    return store.upload(source, filename, str(uuid4()))


def downgrade_to_v1(root: Path) -> None:
    path = root / "metadata.sqlite3"
    with sqlite3.connect(path) as database:
        decks = database.execute("SELECT id, record FROM decks ORDER BY write_order").fetchall()
        operations = database.execute("SELECT id, deck, state, due, record FROM operations ORDER BY write_order").fetchall()
        requests = database.execute("SELECT key, digest, operation FROM requests ORDER BY write_order").fetchall()
        database.execute("DROP TABLE library_state")
        database.execute("DROP TABLE decks")
        database.execute("DROP TABLE operations")
        database.execute("DROP TABLE requests")
        database.executescript("""
            CREATE TABLE decks (id TEXT PRIMARY KEY, record TEXT NOT NULL);
            CREATE TABLE operations (id TEXT PRIMARY KEY, deck TEXT NOT NULL, state TEXT NOT NULL,
                due REAL NOT NULL, record TEXT NOT NULL);
            CREATE TABLE requests (key TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL);
        """)
        database.executemany("INSERT INTO decks VALUES (?, ?)", decks)
        database.executemany("INSERT INTO operations VALUES (?, ?, ?, ?, ?)", operations)
        database.executemany("INSERT INTO requests VALUES (?, ?, ?)", requests)
        database.execute("PRAGMA user_version=1")
    (root / "library.json").unlink()


def test_schema_v1_upgrade_keeps_identity_content_and_original(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    data = b"original presentation bytes"
    accepted = upload(store, tmp_path, "team.pptx", data)
    deleting = upload(store, tmp_path, "pending-delete.pptx", b"pending deletion")
    store.delete(deleting.deck_id)
    store.close()
    downgrade_to_v1(root)

    upgraded = Store(root)
    try:
        deck = upgraded.deck(accepted.deck_id)
        original, _, checksum = upgraded.original(accepted.deck_id)
        marker = json.loads((root / "library.json").read_text(encoding="utf-8"))
        assert deck.deck_id == accepted.deck_id
        assert original == data
        assert checksum == hashlib.sha256(data).hexdigest()
        assert marker["state"] == "active"
        assert UUID(marker["library_id"]) != UUID(marker["database_id"])
        assert (root / "metadata.sqlite3.v1-backup").is_file()
        assert upgraded.operation(accepted.operation_id).operation_id == accepted.operation_id
        assert any(item.deck_id == deleting.deck_id and item.state == "deleting"
                   for item in upgraded.deletions())
    finally:
        upgraded.close()


def test_missing_database_refuses_before_touching_originals(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    accepted = upload(store, tmp_path, "team.pptx", b"keep me")
    original_path = store.source_path(accepted.deck_id)
    before = hashlib.sha256(original_path.read_bytes()).hexdigest()
    store.close()
    (root / "metadata.sqlite3").unlink()

    with pytest.raises(RuntimeError, match="metadata is missing"):
        Store(root)

    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == before


def test_mismatched_marker_refuses_and_releases_owner_lock(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    accepted = upload(store, tmp_path, "team.pptx", b"keep me")
    original_path = store.source_path(accepted.deck_id)
    before = hashlib.sha256(original_path.read_bytes()).hexdigest()
    marker_path = root / "library.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    valid = marker.copy()
    marker["database_id"] = str(uuid4())
    store.close()
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not match"):
        Store(root)

    marker_path.write_text(json.dumps(valid), encoding="utf-8")
    reopened = Store(root)
    try:
        assert hashlib.sha256(original_path.read_bytes()).hexdigest() == before
        assert reopened.deck(accepted.deck_id).deck_id == accepted.deck_id
    finally:
        reopened.close()


def test_incomplete_marker_refuses_without_recovery(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    accepted = upload(store, tmp_path, "team.pptx", b"keep me")
    incoming = root / "incoming" / "unfinished-upload.pptx"
    incoming.write_bytes(b"keep incoming")
    marker_path = root / "library.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["state"] = "preparing"
    store.close()
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(RuntimeError, match="initialization is incomplete"):
        Store(root)

    assert incoming.read_bytes() == b"keep incoming"
    assert (root / "originals" / f"{accepted.deck_id}.pptx").read_bytes() == b"keep me"
    marker["state"] = "active"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    reopened = Store(root)
    reopened.close()


def test_interrupted_v1_upgrade_can_restore_backup_and_retry(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    accepted = upload(store, tmp_path, "team.pptx", b"keep me")
    store.close()
    downgrade_to_v1(root)

    database = sqlite3.connect(root / "metadata.sqlite3")
    backup = sqlite3.connect(root / "metadata.sqlite3.v1-backup")
    database.backup(backup)
    backup.close()
    database.close()
    marker = {"version": 1, "state": "preparing", "library_id": str(uuid4()),
              "database_id": str(uuid4())}
    (root / "library.json").write_text(json.dumps(marker), encoding="utf-8")
    original = root / "originals" / f"{accepted.deck_id}.pptx"
    before = hashlib.sha256(original.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="initialization is incomplete"):
        Store(root)

    assert hashlib.sha256(original.read_bytes()).hexdigest() == before
    shutil.copyfile(root / "metadata.sqlite3.v1-backup", root / "metadata.sqlite3")
    (root / "library.json").unlink()
    resumed = Store(root)
    try:
        assert resumed.deck(accepted.deck_id).deck_id == accepted.deck_id
        assert hashlib.sha256(resumed.source_path(accepted.deck_id).read_bytes()).hexdigest() == before
    finally:
        resumed.close()


def test_invalid_legacy_record_does_not_publish_active_marker(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    accepted = upload(store, tmp_path, "team.pptx", b"keep me")
    original = store.source_path(accepted.deck_id)
    store.close()
    downgrade_to_v1(root)
    with sqlite3.connect(root / "metadata.sqlite3") as database:
        database.execute("UPDATE decks SET record=? WHERE id=?",
                         (json.dumps({"deck_id": str(accepted.deck_id), "filename": "team.pptx",
                                      "checksum": hashlib.sha256(b"keep me").hexdigest(),
                                      "byte_length": len(b"keep me")}), str(accepted.deck_id)))
    incoming = root / "incoming"
    incoming.mkdir(exist_ok=True)
    unfinished = incoming / "unfinished.pptx"
    unfinished.write_bytes(b"preserve")

    with pytest.raises(RuntimeError, match="failed validation"):
        Store(root)

    marker = json.loads((root / "library.json").read_text(encoding="utf-8"))
    assert marker["state"] == "preparing"
    assert original.read_bytes() == b"keep me"
    assert unfinished.read_bytes() == b"preserve"


def test_wrong_version_two_schema_refuses_before_recovery(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    accepted = upload(store, tmp_path, "team.pptx", b"keep me")
    store.close()
    unfinished = root / "incoming" / "unfinished.pptx"
    unfinished.write_bytes(b"preserve")
    with sqlite3.connect(root / "metadata.sqlite3") as database:
        database.execute("DROP TABLE requests")

    with pytest.raises(RuntimeError, match="schema does not match"):
        Store(root)

    assert unfinished.read_bytes() == b"preserve"
    assert (root / "originals" / f"{accepted.deck_id}.pptx").read_bytes() == b"keep me"


def test_write_order_tracks_replacements_and_due_ties(tmp_path: Path) -> None:
    root = tmp_path / "data"
    store = Store(root)
    first = upload(store, tmp_path, "first.pptx", b"one")
    second = upload(store, tmp_path, "second.pptx", b"two")
    try:
        with store.transaction():
            deck = store._deck(first.deck_id)
            store._save_deck(deck)
            first_operation = store._operation(first.operation_id)
            second_operation = store._operation(second.operation_id)
            first_operation.due = second_operation.due = 1
            store._save_operation(first_operation)
            store._save_operation(second_operation)
        assert [deck.deck_id for deck in store.decks()] == [first.deck_id, second.deck_id]
        assert store.metadata is not None
        assert store.metadata.due_operation_ids(1) == [str(first.operation_id), str(second.operation_id)]
    finally:
        store.close()


def test_metadata_transaction_rolls_back_ddl(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    try:
        assert store.metadata is not None and store.metadata.db is not None
        with pytest.raises(RuntimeError, match="interrupt migration"):
            with store.metadata.transaction():
                store.metadata.db.execute("ALTER TABLE decks ADD COLUMN rollback_probe INTEGER")
                raise RuntimeError("interrupt migration")
        columns = {row[1] for row in store.metadata.db.execute("PRAGMA table_info(decks)")}
        assert "rollback_probe" not in columns
    finally:
        store.close()


def test_metadata_database_path_with_spaces_reopens(tmp_path: Path) -> None:
    root = tmp_path / "library data"
    store = Store(root)
    accepted = upload(store, tmp_path, "team.pptx", b"space-safe")
    store.close()

    reopened = Store(root)
    try:
        assert reopened.original(accepted.deck_id)[0] == b"space-safe"
    finally:
        reopened.close()
