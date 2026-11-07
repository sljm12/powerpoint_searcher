from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(base: str, token: str, method: str, route: str, body: bytes | None = None,
            headers: Mapping[str, str] | None = None) -> tuple[int, bytes]:
    request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
    req = Request(f"{base.rstrip('/')}{route}", data=body, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def json_request(base: str, token: str, method: str, route: str,
                 body: bytes | None = None, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
    status, response = request(base, token, method, route, body, headers)
    if status not in (200, 202):
        raise RuntimeError(f"{method} {route} returned HTTP {status}")
    value = json.loads(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"{method} {route} returned an invalid response")
    return value


def poll_deck(base: str, token: str, deck_id: str, terminal: set[str], timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deck = json_request(base, token, "GET", f"/v1/decks/{deck_id}")
        if deck.get("state") in terminal:
            return deck
        time.sleep(1)
    raise TimeoutError(f"Deck {deck_id} did not reach a terminal state")


def poll_deletion(base: str, token: str, deck_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deck = json_request(base, token, "GET", f"/v1/decks/{deck_id}/deletion")
        if deck.get("state") == "deleted":
            return
        time.sleep(1)
    raise TimeoutError(f"Deck {deck_id} was not deleted")


def verify_original(base: str, token: str, deck_id: str, expected: str | None = None) -> str:
    status, original = request(base, token, "GET", f"/v1/decks/{deck_id}/original")
    if status != 200:
        raise RuntimeError(f"Original download returned HTTP {status}")
    checksum = hashlib.sha256(original).hexdigest()
    if expected is not None and checksum != expected:
        raise RuntimeError("Original checksum does not match the uploaded file")
    return checksum


def multipart_body(filename: str, content: bytes, boundary: str) -> bytes:
    if any(ord(character) < 32 or character in {'"', "\\"} for character in filename):
        raise ValueError("Synthetic deck filename contains an unsupported header character")
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body


def upload(base: str, token: str, path: Path) -> str:
    boundary = f"library-check-{uuid.uuid4().hex}"
    body = multipart_body(path.name, path.read_bytes(), boundary)
    result = json_request(base, token, "POST", "/v1/decks", body,
                          {"Content-Type": f"multipart/form-data; boundary={boundary}",
                           "Idempotency-Key": f"workflow-{uuid.uuid4()}"})
    if not isinstance(result.get("deck_id"), str):
        raise RuntimeError("Upload response has no deck ID")
    return result["deck_id"]


def self_check() -> None:
    sample = b"database-workflow-self-check"
    digest = hashlib.sha256(sample).hexdigest()
    assert digest == "f98547ee0223b547860d496a7783f083053acfd0c6dbe9f47b173448de0863be"
    assert hashlib.sha256(sample + b"x").hexdigest() != digest
    body = multipart_body("sample.pptx", sample, "check-boundary")
    assert b'name="file"; filename="sample.pptx"' in body
    assert body.endswith(b"--check-boundary--\r\n") and sample in body


def delete_deck(base: str, token: str, deck_id: str, timeout: float) -> None:
    json_request(base, token, "DELETE", f"/v1/decks/{deck_id}")
    poll_deletion(base, token, deck_id, timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Drive the library HTTP upload, retry, reprocess, and deletion workflow.")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--base-url", default=os.environ.get("LIBRARY_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("LIBRARY_TOKEN"))
    parser.add_argument("--deck", type=Path, help="Synthetic PPTX used for a complete workflow")
    parser.add_argument("--verify-after-restart", help="Deck UUID whose original to check after a manual service restart")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("workflow driver self-check passed")
        return 0
    if not args.token:
        parser.error("Set LIBRARY_TOKEN or pass --token")
    if args.verify_after_restart:
        status = json_request(args.base_url, args.token, "GET", f"/v1/decks/{args.verify_after_restart}")
        checksum = verify_original(args.base_url, args.token, args.verify_after_restart)
        print(json.dumps({"deck_id": args.verify_after_restart, "state": status.get("state"), "sha256": checksum}))
        return 0
    if args.deck is None or not args.deck.is_file() or args.deck.suffix.lower() != ".pptx":
        parser.error("Pass --deck with a synthetic .pptx file")

    deck_id = upload(args.base_url, args.token, args.deck)
    deck = poll_deck(args.base_url, args.token, deck_id, {"extracted", "failed"}, args.timeout)
    operation_id = deck.get("operation_id")
    error = deck.get("error")
    if deck.get("state") == "failed" and isinstance(error, dict) and error.get("retryable"):
        accepted = json_request(args.base_url, args.token, "POST", f"/v1/decks/{deck_id}/retry", b"",
                                {"Idempotency-Key": f"workflow-retry-{uuid.uuid4()}"})
        operation_id = accepted.get("operation_id")
        deck = poll_deck(args.base_url, args.token, deck_id, {"extracted", "failed"}, args.timeout)
    if deck.get("state") != "extracted":
        delete_deck(args.base_url, args.token, deck_id, args.timeout)
        raise RuntimeError("Upload or retry did not reach the extracted state")
    checksum = verify_original(args.base_url, args.token, deck_id, hashlib.sha256(args.deck.read_bytes()).hexdigest())
    accepted = json_request(args.base_url, args.token, "POST", f"/v1/decks/{deck_id}/reprocess", b"",
                            {"Idempotency-Key": f"workflow-reprocess-{uuid.uuid4()}"})
    operation_id = accepted.get("operation_id")
    deck = poll_deck(args.base_url, args.token, deck_id, {"extracted", "failed"}, args.timeout)
    if deck.get("state") != "extracted":
        delete_deck(args.base_url, args.token, deck_id, args.timeout)
        raise RuntimeError("Reprocess did not reach the extracted state")
    delete_deck(args.base_url, args.token, deck_id, args.timeout)
    print(json.dumps({"deck_id": deck_id, "operation_id": operation_id, "state": "deleted", "sha256": checksum}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
