from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest
from tokenizers import Tokenizer

from search_service.models import SearchRequest, SearchResponse, WarningDetail
from search_service.embeddings import Embeddings
from search_service.service import Search
from search_service.settings import Settings
from search_service.store import SearchError, SearchStore


class SlowEmbeddings:
    async def embed(self, inputs: list[str], *, retry_indexing: bool) -> list[list[float]]:
        await asyncio.sleep(0.2)
        return [[1.0]]


class StubStore:
    deadline: float | None = None

    def search(self, request: SearchRequest, vector: list[float] | None,
               degraded_warning: WarningDetail | None, *, deadline: float | None = None) -> SearchResponse:
        self.deadline = deadline
        return SearchResponse(
            query_id=uuid4(), profile_id=request.profile_id,
            mode="lexical" if degraded_warning else "hybrid",
            degraded=degraded_warning is not None,
            warnings=[degraded_warning] if degraded_warning else [],
            coverage={}, decks=[],
        )


def test_large_statement_cap_does_not_reduce_embeddings_to_100ms() -> None:
    settings = Settings(
        host="127.0.0.1", port=8002, token="", database_url="unused",
        database_connect_timeout=2, database_statement_timeout_ms=5_000_000,
        query_timeout=10, embedding_url="http://127.0.0.1:8080/v1",
        embedding_api_key="", embedding_model="model", embedding_expected_model="model",
        embedding_revision="revision", embedding_dimensions=1,
        embedding_request_dimensions=None, query_prefix="", document_prefix="",
        tokenizer_path=Path(__file__).parent / "fixtures" / "fake-tokenizer.json",
        max_input_tokens=128, chunk_tokens=64, chunk_overlap=8, batch_size=1,
        max_batch_tokens=64, embedding_timeout=30, max_attempts=3,
        max_response_bytes=1024, embedding_location="local", allow_cloud=False,
    )
    store = StubStore()
    search = Search(settings, cast(SearchStore, store), cast(Embeddings, SlowEmbeddings()),
                    Tokenizer.from_file(str(settings.tokenizer_path)))
    request = SearchRequest(library_id=uuid4(), profile_id=search.profile_id, query="a query")

    response = asyncio.run(search.query(request))

    assert response.mode == "hybrid"
    assert store.deadline is not None


def test_expired_store_lock_wait_returns_search_timeout() -> None:
    settings = Settings(
        host="127.0.0.1", port=8002, token="", database_url="",
        database_connect_timeout=2, database_statement_timeout_ms=5_000_000,
        query_timeout=10, embedding_url="", embedding_api_key="", embedding_model="",
        embedding_expected_model="", embedding_revision="", embedding_dimensions=0,
        embedding_request_dimensions=None, query_prefix="", document_prefix="",
        tokenizer_path=Path(""), max_input_tokens=0, chunk_tokens=64,
        chunk_overlap=8, batch_size=1, max_batch_tokens=64,
        embedding_timeout=30, max_attempts=3, max_response_bytes=1024,
        embedding_location="local", allow_cloud=False,
    )
    store = SearchStore(settings)
    outcomes: list[SearchError] = []

    def wait_for_store() -> None:
        try:
            with store.connection(deadline=time.monotonic() + 0.1):
                pass
        except SearchError as error:
            outcomes.append(error)

    store.lock.acquire()
    worker = threading.Thread(target=wait_for_store)
    worker.start()
    worker.join(timeout=2)
    store.lock.release()

    assert not worker.is_alive()
    assert len(outcomes) == 1
    assert outcomes[0].status == 504
    assert outcomes[0].code == "search_timeout"


def test_database_errors_are_sanitized_as_service_unavailable() -> None:
    settings = Settings(
        host="127.0.0.1", port=8002, token="", database_url="unused",
        database_connect_timeout=2, database_statement_timeout_ms=5_000_000,
        query_timeout=10, embedding_url="", embedding_api_key="", embedding_model="",
        embedding_expected_model="", embedding_revision="", embedding_dimensions=0,
        embedding_request_dimensions=None, query_prefix="", document_prefix="",
        tokenizer_path=Path(""), max_input_tokens=0, chunk_tokens=64,
        chunk_overlap=8, batch_size=1, max_batch_tokens=64,
        embedding_timeout=30, max_attempts=3, max_response_bytes=1024,
        embedding_location="local", allow_cloud=False,
    )

    class Transaction:
        rolled_back = False

        def __enter__(self) -> Transaction:
            return self

        def __exit__(self, exc_type: Any, *_: Any) -> None:
            self.rolled_back = exc_type is not None

    class FailingConnection:
        transaction_context = Transaction()

        def transaction(self) -> Transaction:
            return self.transaction_context

        def execute(self, query: str, params: Any = None) -> FailingConnection:
            if query.startswith("SELECT library_id"):
                raise psycopg.OperationalError("private database detail")
            return self

    store = SearchStore(settings)
    connection = FailingConnection()
    store.db = cast(psycopg.Connection[dict[str, Any]], connection)
    request = SearchRequest(library_id=uuid4(), profile_id="profile", query="query")

    try:
        store.search(request, None, None, deadline=time.monotonic() + 5)
    except SearchError as error:
        assert error.status == 503
        assert error.code == "storage_unavailable"
        assert "private database detail" not in error.message
    else:
        raise AssertionError("database failure returned a successful search")
    assert connection.transaction_context.rolled_back


def test_deadline_expiring_during_timeout_setup_prevents_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    class Clock:
        value = 0.0

        def monotonic(self) -> float:
            return self.value

    class Transaction:
        def __enter__(self) -> Transaction:
            return self

        def __exit__(self, *_: Any) -> None:
            pass

    class FakeConnection:
        statements: list[str] = []

        def transaction(self) -> Transaction:
            return Transaction()

        def execute(self, query: str, params: Any = None) -> FakeConnection:
            self.statements.append(query)
            if query.startswith("SELECT set_config"):
                clock.value = 11
            return self

        def fetchone(self) -> dict[str, Any]:
            return {"library_id": library_id, "active_profile": "profile", "maintenance": False}

    settings = Settings(
        host="127.0.0.1", port=8002, token="", database_url="unused",
        database_connect_timeout=2, database_statement_timeout_ms=5_000_000,
        query_timeout=10, embedding_url="", embedding_api_key="", embedding_model="",
        embedding_expected_model="", embedding_revision="", embedding_dimensions=0,
        embedding_request_dimensions=None, query_prefix="", document_prefix="",
        tokenizer_path=Path(""), max_input_tokens=0, chunk_tokens=64,
        chunk_overlap=8, batch_size=1, max_batch_tokens=64,
        embedding_timeout=30, max_attempts=3, max_response_bytes=1024,
        embedding_location="local", allow_cloud=False,
    )
    clock = Clock()
    monkeypatch.setattr("search_service.store.time.monotonic", clock.monotonic)
    store = SearchStore(settings)
    connection = FakeConnection()
    store.db = cast(psycopg.Connection[dict[str, Any]], connection)
    library_id = uuid4()
    request = SearchRequest(library_id=library_id, profile_id="profile", query="query")

    with pytest.raises(SearchError) as raised:
        store.search(request, None, None, deadline=10)

    assert raised.value.status == 504
    assert not any(query.startswith("SELECT library_id") for query in connection.statements)
