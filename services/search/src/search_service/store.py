from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import (DeckMatches, IndexRequest, JobAccepted, JobError, JobStatus, Match, SearchRequest,
                     SearchResponse, WarningDetail)
from .settings import Settings


class SearchError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


class SearchStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db: psycopg.Connection[dict[str, Any]] | None = None
        # shortcut: serializes database calls for initial capacity, replace after contention measurements.
        self.lock = threading.RLock()

    def open(self, *, migrate: bool = False) -> None:
        if not self.settings.database_url:
            return
        self.db = psycopg.connect(
            self.settings.database_url,
            autocommit=True,
            connect_timeout=self.settings.database_connect_timeout,
            row_factory=dict_row,
        )
        if self.settings.embedding_dimensions:
            self.migrate() if migrate else self.check_schema()

    def close(self) -> None:
        with self.lock:
            if self.db:
                self.db.close()
                self.db = None

    def register_profile(self, profile_id: str, configuration: dict[str, object]) -> None:
        with self.connection() as connection, connection.transaction():
            row = connection.execute("SELECT configuration FROM search.profiles WHERE profile_id=%s FOR UPDATE",
                                     (profile_id,)).fetchone()
            if row and row["configuration"] != configuration:
                raise RuntimeError("Embedding profile fingerprint does not match its stored configuration")
            connection.execute("INSERT INTO search.profiles (profile_id,configuration) VALUES (%s,%s) "
                               "ON CONFLICT (profile_id) DO NOTHING", (profile_id, Jsonb(configuration)))

    @contextmanager
    def connection(self, *, deadline: float | None = None) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        if deadline is None:
            self.lock.acquire()
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.lock.acquire(timeout=remaining):
                raise SearchError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True)
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise SearchError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True)
            if self.db is None:
                raise SearchError(503, "storage_unavailable", "Search database is not configured.")
            connection = self.db
            if deadline is None:
                connection.execute("SELECT set_config('statement_timeout', %s, false)",
                                   (f"{self.settings.database_statement_timeout_ms}ms",))
            yield connection
        finally:
            self.lock.release()

    def migrate(self) -> None:
        assert self.db is not None
        sql_path = Path(__file__).parent / "migrations" / "001.sql"
        with self.connection() as connection, connection.transaction():
            extension = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname='vector'"
            ).fetchone()
            if not extension:
                raise RuntimeError("Install the pgvector extension in the search database before startup")
            schema = connection.execute("SELECT to_regnamespace('search') AS schema").fetchone()
            if schema is None or schema["schema"] is None:
                unrelated = connection.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_type='BASE TABLE' "
                    "AND table_schema NOT IN ('pg_catalog','information_schema') LIMIT 1"
                ).fetchone()
                if unrelated:
                    raise RuntimeError("Search database must not contain unrelated tables")
            sql = sql_path.read_text(encoding="utf-8").replace(
                "__VECTOR_DIMENSIONS__", str(self.settings.embedding_dimensions)
            )
            connection.execute(sql, prepare=False)
            connection.execute("UPDATE search.service_state SET embedding_dimensions=%s "
                               "WHERE id=1 AND embedding_dimensions=0",
                               (self.settings.embedding_dimensions,))
        self.check_schema()

    def check_schema(self) -> None:
        with self.connection() as connection:
            extension = connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            if not extension:
                raise RuntimeError("Install the pgvector extension in the search database before startup")
            schema = connection.execute("SELECT to_regnamespace('search') AS schema").fetchone()
            if not schema or schema["schema"] is None:
                raise RuntimeError("Run the search-owned migrations before startup")
            version = connection.execute("SELECT version FROM search.schema_version WHERE id=1").fetchone()
            if version is None or version["version"] != 2:
                raise RuntimeError("Search database schema version is unsupported")
            profile_table = connection.execute("SELECT to_regclass('search.profiles') AS profile_table").fetchone()
            if profile_table is None or profile_table["profile_table"] is None:
                raise RuntimeError("Search profile registry is missing; run the search-owned migration")
            state = connection.execute("SELECT embedding_dimensions FROM search.service_state WHERE id=1").fetchone()
            if state is None or state["embedding_dimensions"] != self.settings.embedding_dimensions:
                raise RuntimeError("Embedding dimension changed; run the documented profile migration")

    def health(self) -> tuple[bool, str | None]:
        if self.db is None:
            return False, "database_unconfigured"
        try:
            with self.connection() as connection:
                connection.execute("SELECT 1")
                return True, None
        except Exception:
            return False, "database_unavailable"

    def submit(self, key: str, request: IndexRequest, body: dict[str, Any]) -> JobAccepted:
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        job_id = uuid.uuid4()
        hidden = sum(slide.hidden for slide in request.slides)
        eligible = len(request.slides) - hidden
        searchable = sum(not slide.hidden and bool(slide.native_text.strip() or slide.notes_text.strip())
                         for slide in request.slides)
        blank = eligible - searchable
        with self.connection() as connection, connection.transaction():
            connection.execute(
                "INSERT INTO search.decks (library_id,deck_id,requested_revision,deleted) "
                "VALUES (%s,%s,0,FALSE) ON CONFLICT (library_id,deck_id) DO NOTHING",
                (request.library_id, request.deck_id),
            )
            row = connection.execute(
                "SELECT requested_revision, deleted FROM search.decks "
                "WHERE library_id=%s AND deck_id=%s FOR UPDATE",
                (request.library_id, request.deck_id),
            ).fetchone()
            if row and row["deleted"]:
                raise SearchError(410, "deck_deleted", "This deck has a permanent deletion fence.")
            existing = connection.execute(
                "SELECT id, payload_sha256, state FROM search.jobs WHERE idempotency_key=%s FOR UPDATE",
                (key,),
            ).fetchone()
            if existing:
                if existing["payload_sha256"] != digest:
                    raise SearchError(409, "idempotency_conflict", "The request key was used with different input.")
                return JobAccepted(job_id=existing["id"], state=existing["state"],
                                   status_url=f"/v1/index-jobs/{existing['id']}")
            if row and request.index_revision < row["requested_revision"]:
                raise SearchError(409, "stale_revision", "A newer index revision is already requested.")
            if row and request.index_revision == row["requested_revision"]:
                same_revision = connection.execute(
                    "SELECT id,payload_sha256,state FROM search.jobs WHERE library_id=%s AND deck_id=%s "
                    "AND index_revision=%s FOR UPDATE",
                    (request.library_id, request.deck_id, request.index_revision),
                ).fetchone()
                if same_revision:
                    if same_revision["payload_sha256"] != digest:
                        raise SearchError(409, "revision_conflict", "This index revision was submitted with different input.")
                    return JobAccepted(job_id=same_revision["id"], state=same_revision["state"],
                                       status_url=f"/v1/index-jobs/{same_revision['id']}")
            service = connection.execute(
                "SELECT library_id, active_profile, maintenance FROM search.service_state WHERE id=1 FOR UPDATE"
            ).fetchone()
            if service is None:
                raise SearchError(503, "search_unconfigured", "Search is not configured.")
            if service["library_id"] and service["library_id"] != request.library_id:
                raise SearchError(409, "library_conflict", "This search database is bound to another library.")
            if service["maintenance"]:
                raise SearchError(503, "reindex_required", "Search is paused for profile maintenance.")
            active_profile = service["active_profile"]
            if active_profile and active_profile != request.profile_id:
                raise SearchError(409, "reindex_required", "A profile change requires a full reindex.")
            profile = connection.execute("SELECT 1 FROM search.profiles WHERE profile_id=%s", (request.profile_id,)).fetchone()
            if profile is None:
                raise SearchError(409, "profile_conflict", "Index profile is not registered by this service.")
            connection.execute(
                "UPDATE search.service_state SET library_id=COALESCE(library_id,%s), "
                "active_profile=COALESCE(active_profile,%s) WHERE id=1",
                (request.library_id, request.profile_id),
            )
            connection.execute(
                "INSERT INTO search.decks (library_id,deck_id,requested_revision,deleted) "
                "VALUES (%s,%s,%s,FALSE) ON CONFLICT (library_id,deck_id) DO UPDATE "
                "SET requested_revision=EXCLUDED.requested_revision, active_revision=NULL, "
                "active_generation=NULL, profile_id=NULL, eligible_slides=%s, searchable_slides=%s, "
                "blank_slides=%s, hidden_slides=%s WHERE search.decks.deleted=FALSE",
                (request.library_id, request.deck_id, request.index_revision,
                 eligible, searchable, blank, hidden),
            )
            connection.execute(
                "UPDATE search.jobs SET state='cancelled', payload=NULL, updated_at=now() "
                "WHERE library_id=%s AND deck_id=%s AND index_revision<%s "
                "AND state IN ('queued','processing')",
                (request.library_id, request.deck_id, request.index_revision),
            )
            connection.execute(
                "INSERT INTO search.jobs (id,library_id,deck_id,generation_id,source_revision,"
                "index_revision,profile_id,idempotency_key,payload_sha256,payload,state,eligible_slides,"
                "searchable_slides,blank_slides,hidden_slides) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s)",
                (job_id, request.library_id, request.deck_id, request.generation_id,
                 request.source_revision, request.index_revision, request.profile_id, key,
                 digest, Jsonb(body), eligible, searchable, blank, hidden),
            )
        return JobAccepted(job_id=job_id, state="queued", status_url=f"/v1/index-jobs/{job_id}")

    def status(self, job_id: UUID) -> JobStatus:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM search.jobs WHERE id=%s", (job_id,)).fetchone()
        if not row:
            raise SearchError(404, "job_not_found", "Index job was not found.")
        return JobStatus(
            job_id=row["id"], library_id=row["library_id"], deck_id=row["deck_id"],
            generation_id=row["generation_id"], source_revision=row["source_revision"],
            index_revision=row["index_revision"], profile_id=row["profile_id"], state=row["state"],
            completed_segments=row["completed_segments"], total_segments=row["total_segments"],
            eligible_slides=row["eligible_slides"], searchable_slides=row["searchable_slides"],
            blank_slides=row["blank_slides"], hidden_slides=row["hidden_slides"],
            warnings=[WarningDetail.model_validate(warning) for warning in row["warnings"]],
            error=JobError.model_validate(row["error"]) if row["error"] else None,
        )

    def library_id(self) -> UUID | None:
        with self.connection() as connection:
            row = connection.execute("SELECT library_id FROM search.service_state WHERE id=1").fetchone()
        return row["library_id"] if row else None

    def claim(self) -> dict[str, Any] | None:
        with self.connection() as connection, connection.transaction():
            row = connection.execute(
                "SELECT * FROM search.jobs WHERE state IN ('queued','processing') AND payload IS NOT NULL "
                "ORDER BY updated_at,id FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if not row:
                return None
            connection.execute("UPDATE search.jobs SET state='processing',updated_at=now() WHERE id=%s",
                               (row["id"],))
            return row

    def existing_segments(self, job: dict[str, Any]) -> set[tuple[int, str, int]]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT slide_number,channel,ordinal FROM search.segments "
                "WHERE library_id=%s AND deck_id=%s AND index_revision=%s",
                (job["library_id"], job["deck_id"], job["index_revision"]),
            ).fetchall()
        return {(row["slide_number"], row["channel"], row["ordinal"]) for row in rows}

    def set_total(self, job: dict[str, Any], total: int) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE search.jobs SET total_segments=%s,updated_at=now() WHERE id=%s",
                               (total, job["id"]))

    def save_batch(self, job: dict[str, Any], rows: list[tuple[Any, ...]]) -> bool:
        with self.connection() as connection, connection.transaction():
            deck = connection.execute(
                "SELECT requested_revision,deleted FROM search.decks WHERE library_id=%s AND deck_id=%s FOR UPDATE",
                (job["library_id"], job["deck_id"]),
            ).fetchone()
            if not deck or deck["deleted"] or deck["requested_revision"] != job["index_revision"]:
                connection.execute("UPDATE search.jobs SET state='cancelled',payload=NULL WHERE id=%s",
                                   (job["id"],))
                connection.execute(
                    "DELETE FROM search.segments WHERE library_id=%s AND deck_id=%s AND index_revision=%s",
                    (job["library_id"], job["deck_id"], job["index_revision"]),
                )
                return False
            for row in rows:
                connection.execute(
                    "INSERT INTO search.segments (library_id,deck_id,generation_id,source_revision,"
                    "index_revision,profile_id,slide_number,channel,ordinal,text,embedding,warnings) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s) ON CONFLICT DO NOTHING",
                    (*row[:11], Jsonb(row[11])),
                )
            connection.execute(
                "UPDATE search.jobs SET completed_segments=(SELECT count(*) FROM search.segments "
                "WHERE library_id=%s AND deck_id=%s AND index_revision=%s),updated_at=now() WHERE id=%s",
                (job["library_id"], job["deck_id"], job["index_revision"], job["id"]),
            )
        return True

    def publish(self, job: dict[str, Any], total: int) -> bool:
        with self.connection() as connection, connection.transaction():
            deck = connection.execute(
                "SELECT requested_revision,deleted FROM search.decks WHERE library_id=%s AND deck_id=%s FOR UPDATE",
                (job["library_id"], job["deck_id"]),
            ).fetchone()
            if not deck or deck["deleted"] or deck["requested_revision"] != job["index_revision"]:
                connection.execute("UPDATE search.jobs SET state='cancelled',payload=NULL WHERE id=%s",
                                   (job["id"],))
                return False
            count_row = connection.execute(
                "SELECT count(*) AS count FROM search.segments WHERE library_id=%s AND deck_id=%s AND index_revision=%s",
                (job["library_id"], job["deck_id"], job["index_revision"]),
            ).fetchone()
            if count_row is None:
                raise SearchError(500, "incomplete_build", "Search build did not complete.")
            count = count_row["count"]
            if count != total:
                raise SearchError(500, "incomplete_build", "Search build did not complete.")
            connection.execute(
                "UPDATE search.decks SET active_revision=%s,active_generation=%s,profile_id=%s "
                "WHERE library_id=%s AND deck_id=%s",
                (job["index_revision"], job["generation_id"], job["profile_id"],
                 job["library_id"], job["deck_id"]),
            )
            connection.execute("UPDATE search.jobs SET state='ready',payload=NULL,updated_at=now() WHERE id=%s",
                               (job["id"],))
            connection.execute(
                "DELETE FROM search.segments WHERE library_id=%s AND deck_id=%s AND index_revision<>%s",
                (job["library_id"], job["deck_id"], job["index_revision"]),
            )
        return True

    def fail(self, job: dict[str, Any], code: str, retryable: bool) -> None:
        error = {"code": code, "message": "Search indexing failed. Check search configuration and retry.",
                 "retryable": retryable}
        with self.connection() as connection, connection.transaction():
            connection.execute(
                "UPDATE search.jobs SET state='failed',error=%s,updated_at=now() WHERE id=%s "
                "AND state='processing' AND payload IS NOT NULL AND EXISTS (SELECT 1 FROM search.decks d "
                "WHERE d.library_id=search.jobs.library_id AND d.deck_id=search.jobs.deck_id "
                "AND d.deleted=FALSE AND d.requested_revision=search.jobs.index_revision)",
                (Jsonb(error), job["id"],),
            )

    def retry(self, job_id: UUID, key: str) -> JobAccepted:
        if not key or len(key) > 200:
            raise SearchError(400, "invalid_idempotency_key", "A valid Idempotency-Key is required.")
        with self.connection() as connection, connection.transaction():
            row = connection.execute("SELECT * FROM search.jobs WHERE id=%s FOR UPDATE", (job_id,)).fetchone()
            if not row:
                raise SearchError(404, "job_not_found", "Index job was not found.")
            deck = connection.execute(
                "SELECT deleted FROM search.decks WHERE library_id=%s AND deck_id=%s FOR UPDATE",
                (row["library_id"], row["deck_id"]),
            ).fetchone()
            if deck and deck["deleted"]:
                raise SearchError(410, "deck_deleted", "This deck has a permanent deletion fence.")
            service = connection.execute(
                "SELECT active_profile,maintenance FROM search.service_state WHERE id=1 FOR UPDATE"
            ).fetchone()
            if not service or service["maintenance"] or service["active_profile"] != row["profile_id"]:
                raise SearchError(503, "reindex_required", "This job belongs to an inactive embedding profile.")
            if key in row["retry_keys"]:
                return JobAccepted(job_id=job_id, state=row["state"], status_url=f"/v1/index-jobs/{job_id}")
            if row["state"] != "failed" or not row["payload"] or not row["error"] or not row["error"].get("retryable"):
                raise SearchError(409, "job_not_retryable", "This index job cannot be retried.")
            retry_keys = [*row["retry_keys"], key]
            connection.execute("UPDATE search.jobs SET state='queued',error=NULL,retry_keys=%s,updated_at=now() "
                               "WHERE id=%s", (Jsonb(retry_keys), job_id))
        return JobAccepted(job_id=job_id, state="queued", status_url=f"/v1/index-jobs/{job_id}")

    def delete(self, library_id: UUID, deck_id: UUID) -> None:
        with self.connection() as connection, connection.transaction():
            state = connection.execute("SELECT library_id FROM search.service_state WHERE id=1 FOR UPDATE").fetchone()
            if state and state["library_id"] and state["library_id"] != library_id:
                raise SearchError(409, "library_conflict", "This search database is bound to another library.")
            connection.execute("UPDATE search.service_state SET library_id=COALESCE(library_id,%s) WHERE id=1",
                               (library_id,))
            connection.execute(
                "INSERT INTO search.decks (library_id,deck_id,requested_revision,deleted) VALUES (%s,%s,0,TRUE) "
                "ON CONFLICT (library_id,deck_id) DO UPDATE SET deleted=TRUE,active_revision=NULL,"
                "active_generation=NULL,profile_id=NULL",
                (library_id, deck_id),
            )
            connection.execute(
                "UPDATE search.jobs SET state=CASE WHEN state IN ('ready','failed') THEN state ELSE 'cancelled' END, "
                "payload=NULL,updated_at=now() WHERE library_id=%s AND deck_id=%s",
                (library_id, deck_id),
            )
            connection.execute("DELETE FROM search.segments WHERE library_id=%s AND deck_id=%s",
                               (library_id, deck_id))

    def search(self, request: SearchRequest, vector: list[float] | None,
               degraded_warning: WarningDetail | None, *, deadline: float) -> SearchResponse:
        try:
            with self.connection(deadline=deadline) as connection, connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

                def execute(query: str, params: Any = None) -> Any:
                    remaining_ms = int((deadline - time.monotonic()) * 1000)
                    statement_timeout_ms = min(self.settings.database_statement_timeout_ms, remaining_ms)
                    if statement_timeout_ms <= 0:
                        raise SearchError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True)
                    connection.execute("SELECT set_config('statement_timeout', %s, true)",
                                       (f"{statement_timeout_ms}ms",))
                    if time.monotonic() >= deadline:
                        raise SearchError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True)
                    return connection.execute(query, params)

                state = execute("SELECT library_id,active_profile,maintenance FROM search.service_state WHERE id=1").fetchone()
                if not state or not state["active_profile"]:
                    raise SearchError(503, "search_unconfigured", "Search is not configured.")
                if state["library_id"] != request.library_id:
                    raise SearchError(409, "library_conflict", "This search database is bound to another library.")
                if state["maintenance"]:
                    raise SearchError(503, "reindex_required", "Search is paused for profile maintenance.")
                if request.profile_id != state["active_profile"]:
                    raise SearchError(409, "profile_conflict", "The requested profile is not active.")
                params: list[Any] = [request.library_id, request.profile_id]
                deck_clause = ""
                if request.deck_id:
                    deck_clause = " AND d.deck_id=%s"
                    params.append(request.deck_id)
                lexical: list[dict[str, Any]] = []
                query_row = execute(
                    "SELECT websearch_to_tsquery('simple',%s) AS query, "
                    "querytree(websearch_to_tsquery('simple',%s)) AS positive",
                    (request.query, request.query),
                ).fetchone()
                if query_row and query_row["positive"] not in (None, "", "T"):
                    lexical = execute(
                        "WITH q AS (SELECT websearch_to_tsquery('simple',%s) AS query) "
                        "SELECT s.deck_id,s.slide_number,s.channel,s.ordinal,s.text,s.warnings,"
                        "ts_rank_cd(s.search_vector,q.query) AS score,d.active_generation AS generation_id,"
                        "d.active_revision AS index_revision FROM search.segments s "
                        "JOIN search.decks d ON d.library_id=s.library_id AND d.deck_id=s.deck_id "
                        "CROSS JOIN q WHERE s.library_id=%s AND d.profile_id=%s AND d.deleted=FALSE "
                        "AND d.active_revision=d.requested_revision AND s.index_revision=d.active_revision "
                        "AND s.profile_id=d.profile_id AND s.search_vector @@ q.query" + deck_clause +
                        " ORDER BY score DESC,s.deck_id,s.slide_number,s.ordinal LIMIT %s",
                        [request.query, *params[:2], *params[2:], request.candidate_limit],
                    ).fetchall()
                    lexical = [dict(row) for row in lexical]
                semantic: list[dict[str, Any]] = []
                if vector is not None:
                    vector_text = "[" + ",".join(format(value, ".9g") for value in vector) + "]"
                    semantic = [dict(row) for row in execute(
                        "SELECT s.deck_id,s.slide_number,s.channel,s.ordinal,s.text,s.warnings,"
                        "(s.embedding <=> %s::vector) AS score,d.active_generation AS generation_id,"
                        "d.active_revision AS index_revision FROM search.segments s "
                        "JOIN search.decks d ON d.library_id=s.library_id AND d.deck_id=s.deck_id "
                        "WHERE s.library_id=%s AND d.profile_id=%s AND d.deleted=FALSE "
                        "AND d.active_revision=d.requested_revision AND s.index_revision=d.active_revision "
                        "AND s.profile_id=d.profile_id" + deck_clause + " ORDER BY s.embedding <=> %s::vector, "
                        "s.deck_id,s.slide_number,s.ordinal LIMIT %s",
                        [vector_text, *params, vector_text, request.candidate_limit],
                    ).fetchall()]
                eligible = execute(
                    "SELECT coalesce(sum(d.eligible_slides),0) AS eligible_slides, "
                    "coalesce(sum(d.searchable_slides),0) AS searchable_slides, "
                    "coalesce(sum(d.blank_slides),0) AS blank_slides, "
                    "coalesce(sum(d.hidden_slides),0) AS hidden_slides FROM search.decks d "
                    "WHERE d.library_id=%s AND d.profile_id=%s AND d.deleted=FALSE "
                    "AND d.active_revision=d.requested_revision" + deck_clause,
                    params,
                ).fetchone()
                coverage = {name: int(eligible[name]) for name in (
                    "eligible_slides", "searchable_slides", "blank_slides", "hidden_slides"
                )} if eligible else {"eligible_slides": 0, "searchable_slides": 0, "blank_slides": 0, "hidden_slides": 0}
        except psycopg.errors.QueryCanceled:
            if time.monotonic() >= deadline:
                raise SearchError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True) from None
            raise SearchError(503, "storage_unavailable", "Search database could not complete the query.", True) from None
        except psycopg.Error:
            raise SearchError(503, "storage_unavailable", "Search database is not available.", True) from None
        from .domain import collapse_path, reciprocal_rank_fusion

        lexical = collapse_path(lexical, "score", True)
        semantic = collapse_path(semantic, "score", False)
        mode: Literal["hybrid", "lexical"] = "lexical" if degraded_warning else "hybrid"
        fused = ([{**row, "retrieval_paths": ["lexical"]} for row in lexical]
                 if degraded_warning else reciprocal_rank_fusion(lexical, semantic))
        warnings = [degraded_warning] if degraded_warning else []
        groups: list[DeckMatches] = []
        if request.deck_id:
            slides = fused
            matches = [self._match(row, mode) for row in slides[request.candidate_offset:request.candidate_offset + request.limit]]
            if matches:
                groups = [DeckMatches(deck_id=request.deck_id, matches=matches,
                                      has_more_matches=request.candidate_offset + len(matches) < len(slides))]
        else:
            best: dict[str, dict[str, Any]] = {}
            for row in fused:
                best.setdefault(str(row["deck_id"]), row)
            decks = list(best.values())
            page = decks[request.candidate_offset:request.candidate_offset + request.limit]
            groups = [DeckMatches(
                deck_id=UUID(str(row["deck_id"])), matches=[self._match(row, mode)],
                has_more_matches=sum(item["deck_id"] == row["deck_id"] for item in fused) > 1,
            ) for row in page]
        if time.monotonic() >= deadline:
            raise SearchError(504, "search_timeout", "Search exceeded its configured response deadline. Try again.", True)
        return SearchResponse(
            query_id=uuid.uuid4(), profile_id=request.profile_id, mode=mode,
            degraded=degraded_warning is not None, warnings=warnings,
            coverage=coverage,
            decks=groups,
            next_offset=request.candidate_offset + request.limit if len(groups) == request.limit else None,
        )

    @staticmethod
    def _match(row: dict[str, Any], mode: str) -> Match:
        evidence = row.get("lexical_segment") if mode == "hybrid" and row.get("lexical_segment") else row
        assert isinstance(evidence, dict)
        paths = row.get("retrieval_paths", [])
        assert isinstance(paths, list)
        warnings = evidence.get("warnings", [])
        return Match(
            slide_number=int(row["slide_number"]), generation_id=row["generation_id"],
            index_revision=int(row["index_revision"]), score=float(row["score"]),
            score_kind="lexical" if mode == "lexical" else "rrf",
            retrieval_paths=paths,
            matched_channel="notes" if evidence["channel"] == "notes" else "native",
            snippet=str(evidence["text"])[:500],
            warning_metadata=[WarningDetail.model_validate(item) for item in warnings],
        )
