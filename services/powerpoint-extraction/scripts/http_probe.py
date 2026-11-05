"""Exercise the executable through real HTTP, including crash recovery and deletion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from powerpoint_extraction.fixtures import generate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8018)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if args.data_dir.exists():
        raise ValueError("Use a new dedicated private data directory for this probe")
    token = os.environ["EX1_TOKEN"]
    environment = {**os.environ, "EX1_DATA_DIR": str(args.data_dir), "EX1_PORT": str(args.port), "EX1_HOST": "127.0.0.1"}
    log = (evidence / "server.log").open("ab")
    server: subprocess.Popen[bytes] | None = None
    client = httpx.Client(base_url=f"http://127.0.0.1:{args.port}", headers={"Authorization": f"Bearer {token}"}, timeout=20)
    records: dict[str, Any] = {"platform": platform.platform(), "python": sys.version}

    def start() -> subprocess.Popen[bytes]:
        process = subprocess.Popen([sys.executable, "-m", "powerpoint_extraction"], env=environment, stdout=log, stderr=log)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            assert process.poll() is None, "Executable exited during startup"
            try:
                response = client.get("/health")
                if response.status_code == 200 and response.json()["ready"]:
                    return process
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise AssertionError("Real confined renderer did not become ready")

    def submit(deck: str, source: Path, revision: int = 1, key: str = "first") -> dict[str, Any]:
        response = client.post(f"/v1/decks/{deck}/extractions", files={"file": (source.name, source.read_bytes())}, data={"source_revision": str(revision), "idempotency_key": key})
        assert response.status_code == 202, response.text
        return response.json()

    def poll(url: str, states: tuple[str, ...] = ("succeeded", "failed")) -> dict[str, Any]:
        deadline = time.monotonic() + 190
        while time.monotonic() < deadline:
            response = client.get(url)
            assert response.status_code == 200, response.text
            result = response.json()
            if result["state"] in states:
                return result
            time.sleep(0.05)
        raise AssertionError("HTTP job did not reach the required state")

    def manifest(job: dict[str, Any], label: str) -> dict[str, Any]:
        assert job["state"] == "succeeded", job
        response = client.get(job["manifest_url"])
        assert response.status_code == 200, response.text
        value = response.json()
        (evidence / f"{label}-manifest.json").write_text(json.dumps(value, indent=2))
        for asset in value["assets"]:
            download = client.get(asset["download_url"])
            assert download.status_code == 200
            assert len(download.content) == asset["byte_length"]
            assert hashlib.sha256(download.content).hexdigest() == asset["sha256"]
            assert download.headers["etag"] == f'"{asset["sha256"]}"'
            if asset["role"] == "original_picture":
                assert asset["local_id"] == asset["sha256"]
            if asset["role"] == "slide_preview":
                (evidence / f"{label}-{asset['local_id']}.png").write_bytes(download.content)
        records[label] = {"job": job, "slides": len(value["content"]["slides"]), "assets": len(value["assets"]), "source_sha256": value["source_sha256"]}
        return value

    try:
        server = start()
        source = generate(evidence / "representative.pptx")
        deck = str(uuid4())
        job = submit(deck, source)
        assert submit(deck, source)["job_id"] == job["job_id"]
        completed = poll(job["status_url"])
        first = manifest(completed, "ordinary")
        assert first["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert client.get(completed["manifest_url"], headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(first["assets"][0]["download_url"], headers={"Authorization": "Bearer wrong"}).status_code == 401
        second = submit(deck, source, 2, "revision-2")
        assert client.get(f"/v1/decks/{deck}/manifest").json()["generation_id"] == first["generation_id"]
        replacement = manifest(poll(second["status_url"]), "replacement")
        assert replacement["generation_id"] != first["generation_id"]
        assert client.get(completed["manifest_url"]).status_code == 404
        assert client.delete(f"/v1/decks/{deck}").status_code == 202
        records["delete"] = poll(f"/v1/decks/{deck}/deletion", ("complete",))
        assert client.get(replacement["assets"][0]["download_url"]).status_code == 404
        assert client.get(f"/v1/decks/{deck}/manifest").status_code == 410
        replay = client.post(f"/v1/decks/{deck}/extractions", files={"file": (source.name, source.read_bytes())}, data={"source_revision": "3", "idempotency_key": "deleted"})
        assert replay.status_code == 410
        deleting = str(uuid4())
        pending = submit(deleting, source)
        records["delete_processing_job"] = poll(pending["status_url"], ("processing",))
        assert client.delete(f"/v1/decks/{deleting}").status_code == 202
        records["delete_processing"] = poll(f"/v1/decks/{deleting}/deletion", ("complete",))
        assert client.get(f"/v1/decks/{deleting}/manifest").status_code == 410
        recovering = str(uuid4())
        interrupted = submit(recovering, source)
        poll(interrupted["status_url"], ("processing",))
        server.kill()
        server.wait(timeout=10)
        server = start()
        failed = poll(interrupted["status_url"])
        assert failed["state"] == "failed" and failed["error"]["code"] == "worker_interrupted"
        records["restart_failure"] = failed
        retry = client.post(interrupted["status_url"] + "/retry")
        assert retry.status_code == 202
        manifest(poll(retry.json()["status_url"]), "restart-retry")
        smartart = Path(__file__).resolve().parents[1] / "fixtures" / "smartart-chevron.pptx"
        manifest(poll(submit(str(uuid4()), smartart)["status_url"]), "smartart")
        invalid = client.post(f"/v1/decks/{uuid4()}/extractions", files={"file": ("invalid.pptx", b"not a zip")}, data={"source_revision": "1", "idempotency_key": "invalid"})
        assert invalid.status_code == 422, invalid.text
        records["invalid"] = invalid.json()
        records["passed"] = True
    finally:
        (evidence / "responses.json").write_text(json.dumps(records, indent=2))
        if server is not None and server.poll() is None:
            server.terminate()
            server.wait(timeout=30)
        client.close()
        log.close()


if __name__ == "__main__":
    main()
