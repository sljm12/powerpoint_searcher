from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from search_service.api import create_app
from search_service.settings import Settings


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
