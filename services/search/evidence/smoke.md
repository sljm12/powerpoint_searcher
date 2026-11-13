# Search service smoke evidence

Date: 2026-11-09

Database: the dedicated search database configured by `set_db.bat`; PostgreSQL 18.6, pgvector 0.8.7. No library metadata database or library deck content was read.

## HTTP smoke

Using the deterministic local 8-dimensional fake embedding endpoint, the service reported `live=true`, `ready=true`. A two-slide test deck was submitted; slide 1 contained native and notes text and slide 2 was hidden. The indexing job reached `ready` with two text segments. A hybrid query returned the visible slide 1, with a native-text match. After the deck deletion endpoint committed its fence, the same query returned zero deck groups. The only test deck was deleted; its permanent deletion fence remains in the dedicated search database.

This HTTP run preceded schema version 2 and profile-registry wiring. After those changes, `uv run --locked python -m search_service.migrate` completed successfully and the final search, library, and frontend unit/type/build checks passed. A second live HTTP run against the final profile-registry code was not completed.

The fake vectors establish API/database flow only. They do not measure ranking quality, multilingual retrieval, model compatibility, or performance.
