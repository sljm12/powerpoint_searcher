# Search service smoke evidence

Date: 2026-11-09. Additional live verification: 2026-11-14.

Database: the dedicated search database configured by `set_db.bat`; PostgreSQL 18.6, pgvector 0.8.7. No library metadata database or library deck content was read.

## HTTP smoke

Using the deterministic local 8-dimensional fake embedding endpoint, the service reported `live=true`, `ready=true`. A two-slide test deck was submitted; slide 1 contained native and notes text and slide 2 was hidden. The indexing job reached `ready` with two text segments. A hybrid query returned the visible slide 1, with a native-text match. After the deck deletion endpoint committed its fence, the same query returned zero deck groups. The only test deck was deleted; its permanent deletion fence remains in the dedicated search database.

This HTTP run preceded schema version 2 and profile-registry wiring. After those changes, `uv run --locked python -m search_service.migrate` completed successfully and the final search, library, and frontend unit/type/build checks passed. A second live HTTP run against the final profile-registry code was not completed.

The fake vectors establish API/database flow only. They do not measure ranking quality, multilingual retrieval, model compatibility, or performance.

## Live service check

Date: 2026-11-14. The library, extraction, and search health endpoints on ports 8500, 8501, and 8502 all reported ready. Authenticated policy showed notes enabled and cloud embedding.

A read-only library query with a synthetic phrase reached search before the timeout fix. It returned `mode=lexical`, `degraded=true`, and zero deck groups. Coverage was 78 eligible slides: 12 searchable, 66 blank, and 0 hidden. Search returned one safe warning. The cause was the configured 5,000-second PostgreSQL statement cap being subtracted from the 10-second request deadline, leaving the 100 ms embedding timeout floor.

After the deadline fix and final search-service restart, one authenticated direct search request used only the synthetic query `circular process diagram`. All three health endpoints reported ready. Search returned `mode=hybrid`, `degraded=false`, 2 deck groups, 0 warnings, and the same coverage counts. The response snippets and deck identifiers were not recorded. The test therefore confirms a successful embedding and hybrid retrieval at the search API. It does not retest the library gateway after the restart.

The earlier library request confirms library-to-search routing and lexical fallback. The later direct search request confirms semantic embedding and hybrid retrieval. Neither request establishes relevance quality or performance. No deck content was returned or recorded in this receipt.

## PostgreSQL-backed integration

Date: 2026-11-14. The target from `services/search/.env` was PostgreSQL 18.6, pgvector 0.8.7, database `vectordb`, schema version 2. The search-owned migration/probe completed with `uv run --env-file .env --locked python -m search_service.migrate`.

An authenticated live API run used fresh synthetic UUIDs and text. Two index jobs completed for the same source revision with index revisions 1 and 2. The run verified duplicate idempotency-key submission, retrieval at each active revision, stale-revision rejection, deletion, no results after deletion, and a permanent 410 fence against later indexing. SQL checks confirmed the test jobs had null payloads, their segments were absent, and no temporary library test databases remained. The intentional deletion fence and job receipts remain in the disposable search database.

After the database run, `uv run --env-file .env --locked pytest -q` passed all 12 tests; mypy and Ruff passed. This verifies current-profile indexing and deletion against PostgreSQL. It does not test the unimplemented profile-maintenance/reindex workflow, multilingual relevance, or performance.

## Code checks

Run from `services/search/` on 2026-11-14:

| Command | Result |
| --- | --- |
| `uv run --locked pytest -q` | 12 passed, including the large-statement-cap regression, bounded lock wait, deadline expiry during timeout setup, and sanitized database-error mapping. |
| `uv run --locked mypy src tests` | No issues in 14 source and test files. |
| `uv run --locked ruff check src tests scripts` | All checks passed. |
