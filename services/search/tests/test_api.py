from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from search_service.api import create_app
from search_service.settings import Settings
from search_service.store import SearchStore
from test_embeddings import settings as embedding_settings


def test_health_stays_live_and_unready_without_configuration() -> None:
    settings = Settings(
        host="127.0.0.1", port=8002, token="", database_url="",
        database_connect_timeout=2,
        database_statement_timeout_ms=1000, query_timeout=10,
        embedding_url="", embedding_api_key="", embedding_model="",
        embedding_expected_model="", embedding_revision="", embedding_dimensions=0,
        embedding_request_dimensions=None, query_prefix="", document_prefix="",
        tokenizer_path=Path(""), max_input_tokens=0, chunk_tokens=256,
        chunk_overlap=32, batch_size=1, max_batch_tokens=256, embedding_timeout=30,
        max_attempts=3, max_response_bytes=1024, embedding_location="local", allow_cloud=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"live": True, "ready": False, "error": "search_unconfigured"}


def test_authentication_and_size_checks_precede_json_parsing() -> None:
    settings = Settings(
        host="127.0.0.1", port=8002, token="test-secret", database_url="",
        database_connect_timeout=2, database_statement_timeout_ms=1000, query_timeout=10,
        embedding_url="", embedding_api_key="", embedding_model="",
        embedding_expected_model="", embedding_revision="", embedding_dimensions=0,
        embedding_request_dimensions=None, query_prefix="", document_prefix="",
        tokenizer_path=Path(""), max_input_tokens=0, chunk_tokens=256,
        chunk_overlap=32, batch_size=1, max_batch_tokens=256, embedding_timeout=30,
        max_attempts=3, max_response_bytes=1024, embedding_location="local", allow_cloud=False,
    )
    with TestClient(create_app(settings)) as client:
        unauthorized = client.post("/v1/search", content=b"not-json", headers={"Authorization": "Bearer wrong"})
        oversized = client.post("/v1/search", content=b"x" * (8 * 1024 * 1024 + 1),
                                headers={"Authorization": "Bearer test-secret"})
    assert unauthorized.status_code == 401
    assert oversized.status_code == 413


def test_configured_empty_corpus_returns_no_hits_without_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    config = replace(embedding_settings(), tokenizer_path=Path(__file__).parent / "fixtures/fake-tokenizer.json")
    monkeypatch.setattr(SearchStore, "open", lambda store: setattr(store, "db", object()))
    monkeypatch.setattr(SearchStore, "close", lambda _: None)
    monkeypatch.setattr(SearchStore, "register_profile", lambda *_: None)
    monkeypatch.setattr(SearchStore, "profile_state", lambda *_args, **_kwargs: (None, None, False))
    monkeypatch.setattr(SearchStore, "claim", lambda _: None)
    transport = httpx.MockTransport(lambda _: pytest.fail("An empty corpus should not call the model"))
    auth = {"Authorization": f"Bearer {config.token}"}
    with TestClient(create_app(config, transport)) as client:
        profile = client.get("/v1/policy", headers=auth).json()["profile_id"]
        body = {"library_id": str(uuid4()), "profile_id": profile, "query": "synthetic fixture"}
        response = client.post("/v1/search", headers=auth, json=body)
        assert response.status_code == 200
        assert response.json()["decks"] == []
        assert response.json()["coverage"] == dict.fromkeys(
            ("eligible_slides", "searchable_slides", "blank_slides", "hidden_slides"), 0
        )
        assert client.post("/v1/search", headers=auth, json=body | {"query": " "}).status_code == 400


def test_unreachable_database_is_reported_at_readiness() -> None:
    config = replace(embedding_settings(), database_url="postgresql://127.0.0.1:1/unreachable",
                     tokenizer_path=Path(__file__).parent / "fixtures/fake-tokenizer.json")
    with TestClient(create_app(config)) as client:
        health = client.get("/health")
        assert health.json() == {"live": True, "ready": False, "error": "database_unavailable"}
        assert client.get("/v1/policy", headers={"Authorization": f"Bearer {config.token}"}).status_code == 503
