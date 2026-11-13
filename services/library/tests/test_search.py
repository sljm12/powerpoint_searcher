from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx

from library_service.search import SearchClient, SearchError


def test_search_client_validates_policy_and_reuses_the_stable_submission_key() -> None:
    library_id, job_id = uuid4(), uuid4()
    keys: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer " + "s" * 32
        if request.url.path == "/v1/policy":
            return httpx.Response(200, json={"library_id": str(library_id), "profile_id": "profile",
                "model": "local-model", "location": "local", "disclosure": "Text is sent locally."})
        keys.append(request.headers["idempotency-key"])
        return httpx.Response(202, json={"job_id": str(job_id), "state": "queued",
                                          "status_url": f"/v1/index-jobs/{job_id}"})

    async def run() -> None:
        client = SearchClient("http://127.0.0.1:8002", "s" * 32, transport=httpx.MockTransport(respond))
        try:
            policy = await client.policy()
            assert policy.library_id == library_id and policy.profile_id == "profile"
            job = await client.submit({"library_id": str(library_id)}, "stable-key")
            assert job.job_id == job_id and job.state == "queued"
        finally:
            await client.close()

    asyncio.run(run())
    assert keys == ["stable-key"]


def test_search_client_sanitizes_unavailable_responses() -> None:
    async def run() -> None:
        client = SearchClient("http://127.0.0.1:8002", "s" * 32,
                              transport=httpx.MockTransport(lambda _: httpx.Response(503, text="sensitive")))
        try:
            try:
                await client.policy()
            except SearchError as error:
                assert error.retryable and "sensitive" not in error.message
            else:
                raise AssertionError("Expected unavailable search error")
        finally:
            await client.close()

    asyncio.run(run())
