from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from library_service.api import Settings, create_app
from library_service.metadata import Metadata
from library_service.migrate import migrate
from library_service.store import Store


TOKEN = "library-test-token-0123456789-abcdefghij"
EXTRACTION_TOKEN = "extraction-test-token-0123456789-abcdefgh"


def test_database_selection_rejects_missing_or_contradictory_dsn() -> None:
    with pytest.raises(ValueError, match="LIBRARY_POSTGRES_DSN"):
        Settings(Path("."), TOKEN, "http://extraction.test", EXTRACTION_TOKEN,
                 database_backend="postgresql")
    with pytest.raises(ValueError, match="requires LIBRARY_DB_BACKEND"):
        Settings(Path("."), TOKEN, "http://extraction.test", EXTRACTION_TOKEN,
                 postgres_dsn="postgresql://secret")


def test_sqlite_health_and_storage_error_are_safe(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "library", TOKEN, "http://extraction.test", EXTRACTION_TOKEN)
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ready": True}
        app.state.library.store.metadata.failed = True
        assert client.get("/health").json() == {"ready": False}
        response = client.get("/v1/decks", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"
        assert "secret" not in response.text


def test_postgresql_store_owner_and_root_binding(postgres_dsn: str, tmp_path: Path) -> None:
    root = tmp_path / "library"
    store = Store(root, "postgresql", postgres_dsn)
    assert store.storage_ready()
    with pytest.raises(RuntimeError, match="already has an owner"):
        Store(tmp_path / "second-root", "postgresql", postgres_dsn)
    store.close()
    with pytest.raises(RuntimeError, match="without its root marker"):
        Store(tmp_path / "second-root", "postgresql", postgres_dsn)
    copied_root = tmp_path / "copied-root"
    copied_root.mkdir()
    shutil.copyfile(root / "library.json", copied_root / "library.json")
    with pytest.raises(RuntimeError, match="different library root"):
        Store(copied_root, "postgresql", postgres_dsn)


def test_sqlite_postgresql_round_trip_and_resume(postgres_dsn: str, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    import library_service.migrate as migration

    root = tmp_path / "round trip"
    original_bytes = b"metadata-only transfer fixture"
    incoming = tmp_path / "deck.pptx"
    incoming.write_bytes(original_bytes)
    sqlite_store = Store(root)
    accepted = sqlite_store.upload(incoming, "deck.pptx", "round-trip-upload")
    unknown_deleted_id = uuid4()
    sqlite_store.delete(unknown_deleted_id)
    sqlite_store.close()

    publish = migration._publish_marker
    monkeypatch.setattr(migration, "_publish_marker",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted before marker")))
    with pytest.raises(RuntimeError, match="interrupted before marker"):
        migrate(root, "postgresql", postgres_dsn)
    journal = json.loads((root / "library-transfer.json").read_text(encoding="utf-8"))
    assert journal["phase"] == "target_active"
    monkeypatch.setattr(migration, "_publish_marker", publish)

    counts = migrate(root, "postgresql", postgres_dsn)
    assert counts == {"decks": 2, "operations": 2, "requests": 1}
    postgres_store = Store(root, "postgresql", postgres_dsn)
    assert postgres_store.original(accepted.deck_id)[0] == original_bytes
    assert postgres_store.deck(unknown_deleted_id).state == "deleting"
    postgres_store.close()

    migrate(root, "sqlite", postgres_dsn)
    sqlite_store = Store(root)
    assert sqlite_store.original(accepted.deck_id)[0] == original_bytes
    assert hashlib.sha256(sqlite_store.original(accepted.deck_id)[0]).hexdigest() == hashlib.sha256(original_bytes).hexdigest()
    assert sqlite_store.deck(unknown_deleted_id).state == "deleting"
    sqlite_store.close()

    earlier = next(root.glob("library-transfer-*.json"))
    first_journal = json.loads(earlier.read_text(encoding="utf-8"))
    archive = root / f"metadata.sqlite3.inactive.{first_journal['source_database_id']}"
    assert archive.is_file()
    copied = tmp_path / "stale copy"
    copied.mkdir()
    shutil.copyfile(archive, copied / "metadata.sqlite3")
    shutil.copyfile(root / "library.json", copied / "library.json")
    stale_marker = json.loads((copied / "library.json").read_text(encoding="utf-8"))
    stale_marker.update(backend="sqlite", database_id=first_journal["source_database_id"])
    (copied / "library.json").write_text(json.dumps(stale_marker), encoding="utf-8")
    with pytest.raises(RuntimeError):
        Metadata(copied, Store._validate_legacy_records)


def test_populated_postgresql_target_refuses_without_touching_source(
        postgres_dsn: str, tmp_path: Path) -> None:
    target = Store(tmp_path / "existing database", "postgresql", postgres_dsn)
    target.close()
    root = tmp_path / "sqlite source"
    original = tmp_path / "preserve.pptx"
    original.write_bytes(b"preserve source on target mismatch")
    store = Store(root)
    accepted = store.upload(original, original.name, "target-mismatch")
    source_path = store.source_path(accepted.deck_id)
    expected = hashlib.sha256(source_path.read_bytes()).hexdigest()
    store.close()

    with pytest.raises(RuntimeError, match="target already contains"):
        migrate(root, "postgresql", postgres_dsn)

    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == expected
    assert not (root / "library-transfer.json").exists()
    restored = Store(root)
    assert restored.original(accepted.deck_id)[0] == b"preserve source on target mismatch"
    restored.close()
