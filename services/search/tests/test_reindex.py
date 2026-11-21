from __future__ import annotations

import os
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from search_service.models import IndexRequest, ReindexActivation, ReindexRequest, SearchRequest, WarningDetail
from search_service.settings import Settings
from search_service.store import SearchError, SearchStore


class ScopedConnection:
    def __init__(self, connection: psycopg.Connection[dict[str, Any]], schema: str) -> None:
        self.connection, self.schema = connection, schema

    def transaction(self) -> Any:
        return self.connection.transaction()

    def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        if query == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return None  # The rollback-only outer transaction sets its isolation before the schema is created.
        return self.connection.execute(re.sub(r"\bsearch\b", self.schema, query), *args, **kwargs)


def test_profile_rebuild_uses_a_transactional_cohort_and_fences_late_work() -> None:
    dsn = os.getenv("SEARCH_REINDEX_VALIDATION_DSN") or os.getenv("SEARCH_DATABASE_URL")
    if not dsn:
        pytest.skip("Set the configured PostgreSQL DSN with pgvector for a rollback-only schema check.")
    settings = replace(Settings.from_environment(), embedding_dimensions=3)
    store = SearchStore(settings)
    schema = f"search_verify_{uuid4().hex}"
    library_id, deck, deleted, rejected, failed, legacy = (uuid4() for _ in range(6))
    first = ReindexRequest(library_id=library_id, operation_id=uuid4(), profile_id="new")

    def submit(identifier: UUID, profile: str, revision: int, text: str = "") -> None:
        request = IndexRequest(library_id=library_id, deck_id=identifier, generation_id=uuid4(),
                               source_revision=1, index_revision=revision, profile_id=profile,
                               slides=[{"slide_number": 1, "native_text": text}])
        store.submit(uuid4().hex, request, request.model_dump(mode="json"))

    def assert_rejected(status: int, code: str, action: Any, *args: Any) -> None:
        with pytest.raises(SearchError) as error:
            action(*args)
        assert (error.value.status, error.value.code) == (status, code)

    with psycopg.connect(dsn, autocommit=True, connect_timeout=5, row_factory=dict_row) as connection:
        with connection.transaction(force_rollback=True):
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            connection.execute(f"CREATE SCHEMA {schema}")
            scoped = ScopedConnection(connection, schema)
            store.db = scoped  # type: ignore[assignment]
            initial = Path(__file__).parents[1] / "src/search_service/migrations/001.sql"
            scoped.execute(initial.read_text(encoding="utf-8").replace("__VECTOR_DIMENSIONS__", "3"))
            store.register_profile("old", {"dimensions": 3})
            scoped.execute("UPDATE search.service_state SET library_id=%s,active_profile='old',embedding_dimensions=3",
                           (library_id,))
            scoped.execute(
                "INSERT INTO search.decks (library_id,deck_id,requested_revision,active_revision,active_generation,profile_id) "
                "VALUES (%s,%s,1,1,%s,'old')", (library_id, legacy, legacy),
            )
            scoped.execute(
                "INSERT INTO search.segments (library_id,deck_id,generation_id,source_revision,index_revision,"
                "profile_id,slide_number,channel,ordinal,text,embedding) "
                "VALUES (%s,%s,%s,1,1,'old',1,'native',0,'legacy vector fixture','[1,0,0]')",
                (library_id, legacy, legacy),
            )
            store.migrate()
            store.migrate()
            old_request = SearchRequest(library_id=library_id, profile_id="old", query="legacy vector fixture")
            preserved = store.search(old_request, [1, 0, 0], None, deadline=time.monotonic() + 5)
            assert preserved.decks[0].deck_id == legacy
            assert preserved.decks[0].matches[0].retrieval_paths == ["lexical", "semantic"]
            store.delete(library_id, legacy)
            store.register_profile("new", {"dimensions": 4})
            submit(deck, "old", 1)
            old_job = store.claim()
            assert old_job and store.publish(old_job, 0)
            submit(deleted, "old", 1)
            late = store.claim()
            assert late and store.begin_reindex(first).state == "building"
            assert not store.save_batch(late, [])
            assert not store.publish(late, 0)

            old_query = SearchRequest(library_id=library_id, profile_id="old", query="sample fixture")
            with pytest.raises(SearchError, match="paused"):
                store.search(old_query, None, None, deadline=time.monotonic() + 5)
            submit(deck, "new", 2, "sample fixture")
            current = store.claim()
            assert current
            assert store.save_batch(current, [(library_id, deck, current["generation_id"], 1, 2, "new",
                                               1, "native", 0, "sample fixture", "[1,0,0,0]", [])])
            assert store.publish(current, 1)
            assert not store.publish(current, 1)
            submit(failed, "new", 1, "failed synthetic build")
            failing_job = store.claim()
            assert failing_job
            store.fail(failing_job, "invalid_embedding_response", False)
            activation = ReindexActivation(**first.model_dump(), ready={deck: 2},
                                           excluded={rejected: 1, failed: 1})
            assert_rejected(409, "reindex_incomplete", store.activate_reindex, activation)
            store.delete(library_id, deleted)
            assert_rejected(410, "deck_deleted", submit, deleted, "new", 2)
            assert store.activate_reindex(activation).state == "complete"
            assert store.activate_reindex(activation).state == "complete"
            assert_rejected(409, "stale_revision", submit, rejected, "new", 1)
            new_query = old_query.model_copy(update={"profile_id": "new"})
            result = store.search(new_query, [1, 0, 0, 0], None, deadline=time.monotonic() + 5)
            assert len(result.decks) == 1 and result.decks[0].deck_id == deck
            assert result.decks[0].matches[0].retrieval_paths == ["lexical", "semantic"]
            warning = WarningDetail(code="semantic_unavailable", message="Synthetic inference outage")
            fallback = store.search(new_query, None, warning, deadline=time.monotonic() + 5)
            assert fallback.degraded and fallback.decks[0].matches[0].retrieval_paths == ["lexical"]
            misses = store.search(new_query.model_copy(update={"query": "noothertoken"}), None, warning,
                                  deadline=time.monotonic() + 5)
            assert misses.mode == "lexical" and misses.degraded and not misses.decks
            second = first.model_copy(update={"operation_id": uuid4()})
            assert store.begin_reindex(second).state == "building"
            assert store.begin_reindex(first).state == "complete"
            assert store.profile_state() == (library_id, "new", True)
        assert connection.execute("SELECT to_regnamespace(%s) AS ns", (schema,)).fetchone() == {"ns": None}
