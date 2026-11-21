"""Check upload, indexing, search, replacement, and deletion through Library HTTP."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import TypeVar
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
from pydantic import BaseModel, ValidationError

from check_database_workflow import multipart_body
from library_service.models import Accepted, Deck, Manifest, SearchResult, UploadPolicy


EVIDENCE = Path(__file__).resolve().parents[2] / "powerpoint-extraction" / "evidence"
RECORD = TypeVar("RECORD", bound=BaseModel)


def fixture(capacity: bool, marker: str) -> tuple[bytes, int]:
    source = EVIDENCE / ("capacity-20261105/fixture.pptx" if capacity else "http-final2/representative.pptx")
    title = b"Quarterly report slide 1" if capacity else b"Source inventory proof"
    part = "ppt/slides/slide1.xml"
    buffer = BytesIO()
    with ZipFile(source) as original, ZipFile(buffer, "w", ZIP_DEFLATED) as deck:
        for name in original.namelist():
            content = original.read(name)
            if name == part:
                if content.count(title) != 1:
                    raise RuntimeError("The synthetic deck template is missing its known slide title")
                content = content.replace(title, title + b" " + marker.encode("ascii"), 1)
            deck.writestr(name, content)
    return buffer.getvalue(), 20 if capacity else 2


def call(client: httpx.Client, method: str, route: str, expected: int | tuple[int, ...],
         label: str | None = None, timings: dict[str, list[float]] | None = None,
         *, body: bytes | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
    start = time.perf_counter()
    response = client.request(method, route, content=body, headers=headers)
    if timings is not None and label is not None:
        timings[label].append(time.perf_counter() - start)
    if response.status_code not in ((expected,) if isinstance(expected, int) else expected):
        raise RuntimeError(f"{method} {route} returned HTTP {response.status_code}, expected {expected}")
    return response


def decode(response: httpx.Response, model: type[RECORD]) -> RECORD:
    try:
        return model.model_validate_json(response.content)
    except ValidationError:
        raise RuntimeError(f"Invalid {model.__name__} response") from None


def poll(client: httpx.Client, deck_id: UUID, operation_id: UUID, timeout: float,
         timings: dict[str, list[float]], retry: bool = True) -> Deck:
    route = f"/v1/decks/{deck_id}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deck = decode(call(client, "GET", route, 200, "deck_status", timings), Deck)
        if deck.deck_id != deck_id or deck.operation_id != operation_id:
            raise RuntimeError(f"Unexpected deck or operation identity for {deck_id}")
        if deck.state == "failed":
            raise RuntimeError(f"Extraction failed for synthetic deck {deck_id}")
        search = deck.search
        if search is not None and search.source_revision != deck.source_revision:
            time.sleep(1)
            continue
        if search is not None and search.state == "failed":
            if retry and search.error is not None and search.error.retryable:
                call(client, "POST", f"{route}/search/retry", 200,
                     headers={"Idempotency-Key": str(uuid4())})
                retry = False
            else:
                raise RuntimeError(f"Indexing failed for synthetic deck {deck_id}")
        elif search is not None and search.state == "ready":
            if (deck.state != "extracted" or search.generation_id != deck.generation_id
                    or search.completed_segments != search.total_segments):
                raise RuntimeError(f"Synthetic deck {deck_id} exposed an incomplete search build")
            return deck
        time.sleep(1)
    raise TimeoutError(f"Synthetic deck {deck_id} did not become searchable")


def search(client: httpx.Client, deck_id: UUID, marker: str, timings: dict[str, list[float]]) -> SearchResult:
    return decode(call(client, "POST", "/v1/search", 200, "query", timings,
                       body=json.dumps({"query": marker, "deck_id": str(deck_id), "limit": 5}).encode(),
                       headers={"Content-Type": "application/json"}), SearchResult)


def verify_match(client: httpx.Client, deck: Deck, marker: str,
                 timings: dict[str, list[float]]) -> str:
    result = search(client, deck.deck_id, marker, timings)
    if len(result.decks) != 1 or result.decks[0].deck_id != deck.deck_id or not result.decks[0].matches:
        raise RuntimeError(f"Search did not return the synthetic deck {deck.deck_id}")
    match = result.decks[0].matches[0]
    if (deck.search is None or match.slide_number != 1 or match.generation_id != deck.generation_id
            or match.index_revision != deck.search.index_revision or "lexical" not in match.retrieval_paths):
        raise RuntimeError(f"Search returned the wrong slide or build for {deck.deck_id}")
    route = f"/v1/decks/{deck.deck_id}"
    manifest = decode(call(client, "GET", f"{route}/manifest", 200), Manifest)
    if (manifest.deck_id != deck.deck_id or manifest.generation_id != deck.generation_id
            or manifest.source_sha256 != deck.source_sha256):
        raise RuntimeError(f"Manifest does not match the current generation of {deck.deck_id}")
    preview_id = manifest.content.slides[0].preview.asset_local_id
    preview = next((asset for asset in manifest.assets if asset.local_id == preview_id), None)
    if preview is None or preview.role != "slide_preview":
        raise RuntimeError(f"Manifest has no preview for the matched slide in {deck.deck_id}")
    preview_route = f"{route}/generations/{manifest.generation_id}/assets/{preview.asset_id}"
    if match.preview_url != preview_route:
        raise RuntimeError(f"Search returned an invalid preview route for {deck.deck_id}")
    body = call(client, "GET", preview_route, 200, "preview_download", timings).content
    if len(body) != preview.byte_length or hashlib.sha256(body).hexdigest() != preview.sha256:
        raise RuntimeError(f"Preview bytes do not match the manifest for {deck.deck_id}")
    return result.mode


def delete(client: httpx.Client, deck_id: UUID, marker: str, timeout: float,
           timings: dict[str, list[float]]) -> None:
    route = f"/v1/decks/{deck_id}"
    call(client, "DELETE", route, 202, "delete_acceptance", timings)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deck = decode(call(client, "GET", f"{route}/deletion", 200), Deck)
        if deck.deck_id != deck_id:
            raise RuntimeError(f"Expected deletion status for {deck_id}")
        if deck.state == "deleted":
            break
        time.sleep(1)
    else:
        raise TimeoutError(f"Cleanup did not finish for synthetic deck {deck_id}")
    call(client, "GET", f"{route}/original", (404, 410))
    if search(client, deck_id, marker, timings).decks:
        raise RuntimeError(f"Deleted deck {deck_id} remained searchable")


def self_check() -> None:
    marker = "libraryfixturecheck"
    for capacity in (False, True):
        content, count = fixture(capacity, marker)
        with ZipFile(BytesIO(content)) as deck:
            assert b"libraryfixturecheck" in deck.read("ppt/slides/slide1.xml")
            slides = [name for name in deck.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
            assert len(slides) == count
            assert deck.testzip() is None
        body = multipart_body("synthetic.pptx", content, "fixture-boundary")
        assert b'name="file"; filename="synthetic.pptx"' in body and content in body
    print("library search workflow fixture self-check passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("LIBRARY_URL", "http://127.0.0.1:8500"))
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--capacity", action="store_true", help="Retain 100 synthetic 20-slide decks until cleanup")
    parser.add_argument("--real-endpoint", action="store_true", help="Allow synthetic text to reach a configured cloud model")
    parser.add_argument("--timeout", type=float, default=300, help="Maximum seconds to wait per deck phase")
    options = parser.parse_args()
    if options.self_check:
        self_check()
        return
    endpoint = httpx.URL(options.base_url)
    if (endpoint.host not in ("127.0.0.1", "localhost", "::1") or endpoint.scheme not in ("http", "https")
            or endpoint.userinfo or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment):
        parser.error("Use a loopback Library service root without credentials.")
    token = os.environ.get("LIBRARY_TOKEN")
    if not token:
        parser.error("Set LIBRARY_TOKEN in the environment.")
    if not math.isfinite(options.timeout) or options.timeout <= 0:
        parser.error("--timeout must be positive and finite.")
    timings: dict[str, list[float]] = defaultdict(list)
    temporary: dict[UUID, str] = {}
    modes: dict[str, int] = defaultdict(int)
    source_slides = 0
    with httpx.Client(base_url=endpoint, headers={"Authorization": f"Bearer {token}"},
                      timeout=30, trust_env=False) as client:
        if call(client, "GET", "/health", 200).json().get("ready") is not True:
            raise RuntimeError("Library is not ready")
        policy = decode(call(client, "GET", "/v1/policy", 200), UploadPolicy).search
        if policy.state != "ready" or policy.maintenance:
            raise RuntimeError("Library search is not ready")
        if policy.location != "local" and not options.real_endpoint:
            raise RuntimeError("Use --real-endpoint to send synthetic fixtures to the configured model.")
        cutoff = time.perf_counter()
        try:
            for _ in range(100 if options.capacity else 1):
                marker = f"libraryfixture{uuid4().hex}"
                body, slides = fixture(options.capacity, marker)
                source_slides += slides
                boundary = f"workflow-{uuid4().hex}"
                upload = multipart_body(f"{marker}.pptx", body, boundary)
                headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
                           "Idempotency-Key": str(uuid4())}
                try:
                    response = call(client, "POST", "/v1/decks", 202, "upload_acceptance", timings,
                                    headers=headers, body=upload)
                except (httpx.TimeoutException, httpx.NetworkError):
                    response = call(client, "POST", "/v1/decks", 202, headers=headers, body=upload)
                accepted = decode(response, Accepted)
                deck_id = accepted.deck_id
                temporary[deck_id] = marker
                if not options.capacity:
                    replay = call(client, "POST", "/v1/decks", 202, headers=headers, body=upload)
                    if decode(replay, Accepted) != accepted:
                        raise RuntimeError("Upload replay returned a different operation")
                deck = poll(client, deck_id, accepted.operation_id, options.timeout, timings)
                if deck.total_slides != slides:
                    raise RuntimeError(f"Extracted slide count did not match the fixture for {deck_id}")
                modes[verify_match(client, deck, marker, timings)] += 1
                route = f"/v1/decks/{deck_id}"
                original = call(client, "GET", f"{route}/original", 200, "original_download", timings)
                if hashlib.sha256(original.content).digest() != hashlib.sha256(body).digest():
                    raise RuntimeError(f"Original bytes changed for {deck_id}")
                if not options.capacity:
                    job = decode(call(client, "POST", f"{route}/reprocess", 202,
                                      headers={"Idempotency-Key": str(uuid4())}), Accepted)
                    replacement = poll(client, deck_id, job.operation_id, options.timeout, timings)
                    if (job.deck_id != deck_id or replacement.generation_id == deck.generation_id
                            or replacement.source_revision <= deck.source_revision
                            or deck.search is None or replacement.search is None
                            or replacement.search.index_revision <= deck.search.index_revision):
                        raise RuntimeError("Reprocessing did not publish a newer source and search generation")
                    modes[verify_match(client, replacement, marker, timings)] += 1
                    call(client, "GET", f"{route}/manifest?generation={deck.generation_id}", 404)
            workload_seconds = time.perf_counter() - cutoff
        finally:
            cleanup_errors = []
            for deck_id, marker in temporary.items():
                try:
                    delete(client, deck_id, marker, options.timeout, timings)
                except (RuntimeError, httpx.HTTPError, TimeoutError) as error:
                    cleanup_errors.append(error)
            if cleanup_errors:
                raise ExceptionGroup("Synthetic deck cleanup verification did not pass", cleanup_errors)

    print(json.dumps({
        "decks": len(temporary), "source_slides": source_slides, "deleted": len(temporary),
        "workload_seconds": round(workload_seconds, 3), "retrieval_modes": dict(modes),
        "measurements": {name: {"samples": len(values),
                                "p95_seconds": round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3)}
                         for name, values in timings.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
