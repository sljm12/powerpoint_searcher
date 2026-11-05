from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from powerpoint_extraction.api import Settings, create_app
from powerpoint_extraction.content import ExtractionContent, Rendition, SlideContent, SlideSource
from powerpoint_extraction.models import ExtractionResult, OutputAsset, Progress, ServiceError
from powerpoint_extraction.store import Store

TOKEN = "test-secret-which-is-at-least-32-bytes"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
IMAGE_BUFFER = BytesIO()
Image.new("RGB", (2, 2), "white").save(IMAGE_BUFFER, format="PNG")
IMAGE_BYTES = IMAGE_BUFFER.getvalue()


def valid_content() -> ExtractionContent:
    return ExtractionContent(extractor_version="test", renderer_version="test", config_fingerprint="0" * 64,
                             slides=[SlideContent(source=SlideSource(number=1, source_slide_id=1, hidden=False, width=100, height=100),
                             preview=Rendition(asset_local_id="preview", status="rendered", method="slide_render", pixel_size=(2, 2), transparent=False), occurrences=[])])


async def good(source: Path, output: Path, progress: Progress) -> ExtractionResult:
    progress("rendering", 0, 1)
    (output / "preview.png").write_bytes(IMAGE_BYTES)
    progress("rendering", 1, 1)
    return ExtractionResult(content=valid_content(),
                            assets=[OutputAsset(local_id="preview", relative_path="preview.png",
                                                media_type="image/png", role="slide_preview", pixel_size=(2, 2))], slide_count=1)


def validate(path: Path) -> None:
    if path.read_bytes() == b"invalid":
        raise ValueError("secret content must not escape")


def upload(client: TestClient, deck: UUID, revision: int = 1, key: str = "first", data: bytes = b"deck") -> Any:
    return client.post(f"/v1/decks/{deck}/extractions", headers=HEADERS,
                       files={"file": ("deck.pptx", data)},
                       data={"source_revision": str(revision), "idempotency_key": key})


def poll(client: TestClient, url: str, field: str = "state", final: tuple[str, ...] = ("succeeded", "failed")) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(url, headers=HEADERS)
        assert response.status_code == 200, response.text
        value: dict[str, Any] = response.json()
        if value[field] in final:
            return value
        time.sleep(0.01)
    raise AssertionError("Operation did not finish")


def test_upload_duplicate_conflict_download_delete(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path, TOKEN), extractor=good, ready=lambda: True,
                     validate_upload=validate, secure_storage=False)
    deck = uuid4()
    with TestClient(app) as client:
        first = upload(client, deck)
        assert first.status_code == 202, first.text
        job = poll(client, first.json()["status_url"])
        assert job["state"] == "succeeded"
        duplicate = upload(client, deck)
        assert duplicate.json()["job_id"] == job["job_id"]
        assert upload(client, deck, key="first", data=b"changed").status_code == 409
        assert upload(client, deck, key="other", data=b"changed").status_code == 409
        assert client.post(job["status_url"] + "/retry", headers=HEADERS).status_code == 409
        manifest = client.get(job["manifest_url"], headers=HEADERS).json()
        assert manifest["source_filename"] == "deck.pptx"
        asset_url = manifest["assets"][0]["download_url"]
        asset = client.get(asset_url, headers=HEADERS)
        assert asset.content == IMAGE_BYTES
        assert asset.headers["etag"] == '"' + hashlib.sha256(IMAGE_BYTES).hexdigest() + '"'
        assert asset.headers["content-length"] == str(len(IMAGE_BYTES))
        assert "attachment" in asset.headers["content-disposition"]
        assert ".png" in asset.headers["content-disposition"]
        assert asset.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/v1/assets/{uuid4()}", headers=HEADERS).status_code == 404
        for url in (job["status_url"], job["manifest_url"], asset_url, f"/v1/decks/{deck}/deletion"):
            assert client.get(url).status_code == 401
        assert client.delete(f"/v1/decks/{deck}", headers=HEADERS).status_code == 202
        assert poll(client, f"/v1/decks/{deck}/deletion", final=("complete",))["error"] is None
        assert client.get(asset_url, headers=HEADERS).status_code == 404
        assert client.get(job["manifest_url"], headers=HEADERS).status_code == 410
        assert upload(client, deck).status_code == 410
        assert not (tmp_path / str(deck)).exists()
        assert client.delete(f"/v1/decks/{deck}", headers=HEADERS).json()["state"] == "complete"
        with app.state.store.lock:
            assert app.state.store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
            assert app.state.store.db.execute("SELECT revision,checksum,filename,current_job,active_generation FROM decks").fetchone()[:] == (None, None, None, None, None)


def test_failed_retry_and_timeout(tmp_path: Path) -> None:
    attempts = 0

    async def fails_once(source: Path, output: Path, progress: Progress) -> ExtractionResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private source path / secret")
        return await good(source, output, progress)

    app = create_app(Settings(tmp_path, TOKEN), extractor=fails_once, ready=lambda: True,
                     validate_upload=validate, secure_storage=False)
    with TestClient(app) as client:
        first = upload(client, uuid4()).json()
        failure = poll(client, first["status_url"])
        assert failure["error"]["retryable"] is True
        assert "secret" not in str(failure)
        retried = client.post(first["status_url"] + "/retry", headers=HEADERS)
        assert retried.status_code == 202
        assert retried.json()["job_id"] != first["job_id"]
        assert poll(client, retried.json()["status_url"])["state"] == "succeeded"
        assert client.post(first["status_url"] + "/retry", headers=HEADERS).status_code == 409

    async def hangs(source: Path, output: Path, progress: Progress) -> ExtractionResult:
        await asyncio.sleep(10)
        return await good(source, output, progress)

    with TestClient(create_app(Settings(tmp_path / "timeout", TOKEN, conversion_timeout=0.05),
                              extractor=hangs, ready=lambda: True, validate_upload=validate, secure_storage=False)) as client:
        response = upload(client, uuid4()).json()
        assert poll(client, response["status_url"])["error"]["code"] == "conversion_timeout"


def test_old_generation_and_deletion_awaits_cancellation(tmp_path: Path) -> None:
    cancelled = False

    async def slow(source: Path, output: Path, progress: Progress) -> ExtractionResult:
        nonlocal cancelled
        if source.read_bytes() == b"new":
            progress("rendering", 0, 1)
            try:
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                await asyncio.sleep(0.15)
                (output / "late.txt").write_bytes(b"last child write")
                cancelled = True
                raise
        return await good(source, output, progress)

    app = create_app(Settings(tmp_path, TOKEN), extractor=slow, ready=lambda: True,
                     validate_upload=validate, secure_storage=False)
    deck, other = uuid4(), uuid4()
    with TestClient(app) as client:
        first = poll(client, upload(client, deck).json()["status_url"])
        old_generation = client.get(first["manifest_url"], headers=HEADERS).json()["generation_id"]
        second = upload(client, deck, revision=2, key="new", data=b"new").json()
        processing = poll(client, second["status_url"], final=("processing",))
        assert processing["stage"] == "rendering"
        assert processing["total_slides"] == 1
        assert client.get(f"/v1/decks/{deck}/manifest", headers=HEADERS).json()["generation_id"] == old_generation
        assert client.get(f"/v1/decks/{other}/manifest?generation={old_generation}", headers=HEADERS).status_code == 404
        assert upload(client, deck, revision=1, key="older").status_code == 409
        client.delete(f"/v1/decks/{deck}", headers=HEADERS)
        assert client.get(f"/v1/decks/{deck}/deletion", headers=HEADERS).json()["state"] == "pending"
        poll(client, f"/v1/decks/{deck}/deletion", final=("complete",))
        assert cancelled
        assert not (tmp_path / str(deck)).exists()
        time.sleep(0.1)
        assert not (tmp_path / str(deck)).exists()


def test_restart_fences_and_atomic_generation_switch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"deck")
    root = tmp_path / "data"
    deck = uuid4()
    store = Store(root)
    first = store.submit(deck, 1, "first", source)
    claim = store.claim()
    assert claim is not None
    with pytest.raises(RuntimeError, match="already has an owner"):
        Store(root)
    store.close()
    store = Store(root)
    assert store.get_job(first.job_id).error is not None
    retried = store.retry(first.job_id)
    current = store.claim()
    assert current is not None
    result = asyncio.run(good(current.source_path, current.output_path, lambda *args: None))
    assert store.publish(current, result)
    old = store.manifest(deck)
    source.write_bytes(b"new")
    store.submit(deck, 2, "second", source)
    newer = store.claim()
    assert newer is not None
    assert store.manifest(deck).generation_id == old.generation_id
    assert not store.publish(current, result)
    new_result = asyncio.run(good(newer.source_path, newer.output_path, lambda *args: None))
    assert store.publish(newer, new_result)
    assert store.manifest(deck).generation_id == newer.job.generation_id
    with pytest.raises(ServiceError) as retired:
        store.manifest(deck, old.generation_id)
    assert retired.value.status == 404
    store.delete(deck)
    assert not store.publish(newer, new_result)
    store.cleanup()
    assert store.deletion(deck).state == "complete"
    store.close()
    store = Store(root)
    with pytest.raises(ServiceError) as error:
        store.submit(deck, 3, "after-delete", source)
    assert error.value.status == 410
    store.close()
    assert retried.attempt == 2


def test_boundary_validation_and_queue_limits(tmp_path: Path) -> None:
    async def slow(source: Path, output: Path, progress: Progress) -> ExtractionResult:
        await asyncio.sleep(10)
        return await good(source, output, progress)

    app = create_app(Settings(tmp_path, TOKEN, upload_limit=64, request_limit=1024, queue_limit=1),
                     extractor=slow, ready=lambda: True, validate_upload=validate, secure_storage=False)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ready": True}
        assert client.post(f"/v1/decks/{uuid4()}/extractions", content=b"x" * 2000).status_code == 401
        assert client.post(f"/v1/decks/{uuid4()}/extractions", content=b"x" * 2000, headers=HEADERS).status_code == 413
        consumed = False

        def oversized_chunks():
            nonlocal consumed
            consumed = True
            yield b"x" * 600
            yield b"y" * 600

        endpoint = f"/v1/decks/{uuid4()}/extractions"
        assert client.post(endpoint, content=oversized_chunks()).status_code == 401
        assert not consumed
        assert client.post(endpoint, content=oversized_chunks(), headers=HEADERS).status_code == 413
        assert upload(client, uuid4(), data=b"x" * 65).status_code == 413
        malformed = upload(client, uuid4(), data=b"invalid")
        assert malformed.status_code == 422
        assert "secret" not in malformed.text
        assert upload(client, uuid4(), key="bad\nkey").status_code == 400
        assert upload(client, uuid4(), revision=0).status_code == 400
        assert client.get("/v1/jobs/not-a-uuid", headers=HEADERS).status_code == 400
        assert client.post(f"/v1/decks/{uuid4()}/extractions", headers={**HEADERS, "Content-Type": "multipart/form-data; boundary=bad"}, content=b"garbage").status_code == 400
        first = upload(client, uuid4())
        assert first.status_code == 202
        assert upload(client, uuid4()).status_code == 429


def test_unconfigured_renderer_is_not_ready(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(tmp_path, TOKEN), secure_storage=False)) as client:
        assert client.get("/health").json() == {"ready": False}
        assert upload(client, uuid4()).status_code == 503
        assert client.get("/openapi.json", headers=HEADERS).status_code == 404


def test_protected_storage_boot() -> None:
    with tempfile.TemporaryDirectory(prefix="ex1-private-boot-") as directory:
        root = Path(directory) / "data"
        with TestClient(create_app(Settings(root, TOKEN))) as client:
            assert client.get("/health").json() == {"ready": False}
            assert root.is_dir()
            if sys.platform == "win32":
                script = "$ErrorActionPreference='Stop'; $s=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $a=Get-Acl -LiteralPath $env:EX1_ACL_CHECK; if(-not $a.AreAccessRulesProtected){exit 2}; $r=@($a.Access); if($r.Count -ne 1 -or $r[0].IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $s){exit 3}"
                subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                               env={**os.environ, "EX1_ACL_CHECK": str(root)}, check=True,
                               timeout=10, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def test_all_content_routes_require_auth_and_schema_declares_errors(tmp_path: Path) -> None:
    app = create_app(Settings(tmp_path, TOKEN), secure_storage=False)
    schema = app.openapi()
    assert "ErrorResponse" in schema["components"]["schemas"]
    with TestClient(app) as client:
        identifier = uuid4()
        routes = [("POST", f"/v1/decks/{identifier}/extractions"),
                  ("GET", f"/v1/jobs/{identifier}"), ("POST", f"/v1/jobs/{identifier}/retry"),
                  ("GET", f"/v1/decks/{identifier}/manifest"), ("GET", f"/v1/assets/{identifier}"),
                  ("DELETE", f"/v1/decks/{identifier}"), ("GET", f"/v1/decks/{identifier}/deletion")]
        for method, url in routes:
            assert client.request(method, url).status_code == 401


def test_output_path_escape_rejected_and_cleanup_recovery(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"deck")
    store = Store(tmp_path / "data")
    deck = uuid4()
    store.submit(deck, 1, "key", source)
    claim = store.claim()
    assert claim is not None
    result = ExtractionResult(content=valid_content(), assets=[OutputAsset(local_id="escape", relative_path="../../../../source", media_type="image/png", role="slide_preview", pixel_size=(2, 2))], slide_count=1)
    with pytest.raises(ValueError, match="Invalid output asset"):
        store.publish(claim, result)
    store.delete(deck)
    store.close()
    store = Store(tmp_path / "data")
    assert store.deletion(deck).state == "complete"
    assert not (store.root / str(deck)).exists()
    store.close()
