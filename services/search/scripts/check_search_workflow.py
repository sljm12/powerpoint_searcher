"""Exercise the configured search service with synthetic decks."""
from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

import httpx


def call(client: httpx.Client, method: str, route: str, expected: int = 200,
         **kwargs: object) -> dict:
    response = client.request(method, route, **kwargs)
    if response.status_code != expected:
        raise RuntimeError(f"{method} {route} returned HTTP {response.status_code}, expected {expected}")
    return response.json()


@contextmanager
def synthetic_index(base_url: str, allow_cloud: bool) -> Iterator[tuple[httpx.Client, dict, list[str]]]:
    endpoint = httpx.URL(base_url)
    if (endpoint.host not in ("127.0.0.1", "localhost", "::1") or endpoint.scheme not in ("http", "https")
            or endpoint.userinfo or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment):
        raise ValueError("Use a loopback Search API root without credentials.")
    token = os.environ["SEARCH_TOKEN"]
    with httpx.Client(base_url=endpoint, headers={"Authorization": f"Bearer {token}"},
                      timeout=30, trust_env=False) as client:
        assert call(client, "GET", "/health")["ready"]
        policy = call(client, "GET", "/v1/policy")
        if policy["location"] == "cloud" and not allow_cloud:
            raise RuntimeError("Use --real-endpoint to send the synthetic fixtures to the configured cloud model.")
        if policy.get("maintenance") or not policy["library_id"]:
            raise RuntimeError("Run against a configured, active search library.")
        deck_ids: list[str] = []
        try:
            yield client, policy, deck_ids
        finally:
            failed = []
            for deck_id in deck_ids:
                try:
                    call(client, "DELETE", f"/v1/decks/{deck_id}", params={"library_id": policy["library_id"]})
                except (RuntimeError, httpx.HTTPError):
                    failed.append(deck_id)
            if failed:
                raise RuntimeError(f"Synthetic decks still require cleanup: {', '.join(failed)}")


def submit(client: httpx.Client, policy: dict, deck_ids: list[str], slides: list[dict],
           *, key: str | None = None, deck_id: str | None = None, generation: str | None = None,
           revision: int = 1, expected: int = 202) -> dict:
    if deck_id is None:
        deck_id = str(uuid4())
        deck_ids.append(deck_id)
    return call(client, "POST", "/v1/index-jobs", expected, headers={"Idempotency-Key": key or uuid4().hex},
                json={"library_id": policy["library_id"], "deck_id": deck_id,
                      "generation_id": generation or str(uuid4()), "source_revision": 1,
                      "index_revision": revision, "profile_id": policy["profile_id"], "slides": slides})


def wait_jobs(client: httpx.Client, jobs: list[dict], timeout: float) -> list[dict]:
    pending = {f"/v1/index-jobs/{UUID(job['job_id'])}" for job in jobs}
    ready = []
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for url in sorted(pending):
            status = call(client, "GET", url)
            if status["state"] in ("failed", "cancelled"):
                raise RuntimeError(f"Index job {status['job_id']} ended in {status['state']}")
            if status["state"] == "ready":
                pending.remove(url)
                ready.append(status)
        if pending:
            time.sleep(1)
    if pending:
        raise RuntimeError(f"{len(pending)} synthetic index jobs did not finish within {timeout:g} seconds")
    return ready


def query(client: httpx.Client, policy: dict, text: str, **filters: object) -> dict:
    return call(client, "POST", "/v1/search", json={"library_id": policy["library_id"],
                "profile_id": policy["profile_id"], "query": text, **filters})


def fixture(label: str, slide_count: int) -> list[dict]:
    topics = ("water conservation", "battery storage", "rail signalling", "emergency communications",
              "crop rotation", "team onboarding", "materials recycling", "public health logistics")
    slides = [{"slide_number": number,
               "native_text": f"Case {label} workstream {number}: {topics[(number - 1) % len(topics)]}"}
              for number in range(1, slide_count + 1)]
    if slide_count == 4:
        slides[1] = {"slide_number": 2, "notes_text": "Volcanic ash ground sensor calibration"}
        slides[2] = {"slide_number": 3}
        slides[3] = {"slide_number": 4, "hidden": True, "native_text": "Hidden specimen"}
    return slides


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8502")
    parser.add_argument("--real-endpoint", action="store_true")
    parser.add_argument("--capacity", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--timeout", type=float, default=1800)
    options = parser.parse_args()
    if options.self_check:
        assert len(fixture("test", 20)) == 20 and fixture("test", 4)[3]["hidden"]
        print("Synthetic fixture self-check passed")
        return
    with synthetic_index(options.base_url, options.real_endpoint) as (client, policy, deck_ids):
        count, slides = (100, 20) if options.capacity else (1, 4)
        start = time.monotonic()
        jobs = [submit(client, policy, deck_ids, fixture(str(index), slides)) for index in range(count)]
        statuses = wait_jobs(client, jobs, options.timeout)
        indexing_seconds = time.monotonic() - start
        assert sum(row["searchable_slides"] for row in statuses) == (count * slides if options.capacity else 2)
        probe = "water conservation"
        result = query(client, policy, probe, deck_id=deck_ids[0])
        assert result["decks"] and result["decks"][0]["matches"][0]["slide_number"] == 1
        if not options.capacity:
            assert query(client, policy, "volcanic ash", deck_id=deck_ids[0])["decks"][0]["matches"][0]["slide_number"] == 2
            key, generation = uuid4().hex, str(uuid4())
            replacement = dict(key=key, deck_id=deck_ids[0], generation=generation, revision=2)
            new = submit(client, policy, deck_ids, fixture("replacement", 4), **replacement)
            assert submit(client, policy, deck_ids, fixture("replacement", 4), **replacement)["job_id"] == new["job_id"]
            wait_jobs(client, [new], options.timeout)
            submit(client, policy, deck_ids, fixture("old", 4), deck_id=deck_ids[0], expected=409)
            call(client, "DELETE", f"/v1/decks/{deck_ids[0]}", params={"library_id": policy["library_id"]})
            assert not query(client, policy, probe, deck_id=deck_ids[0])["decks"]
            submit(client, policy, deck_ids, fixture("late", 4), deck_id=deck_ids[0], revision=3, expected=410)
        print(json.dumps({"decks": count, "slides": count * slides,
                          "segments": sum(row["completed_segments"] for row in statuses),
                          "indexing_seconds": round(indexing_seconds, 3), "query_mode": result["mode"]}))


if __name__ == "__main__":
    main()
