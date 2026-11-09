# PowerPoint search service

This service owns text indexing, exact vector retrieval, lexical retrieval, and slide ranking. It does not read library files or tables. The library sends bounded slide text over HTTP and supplies current preview and download URLs.

## Configure and run

Use a dedicated PostgreSQL 18 database with pgvector 0.8.7 installed by a database administrator. This pairing was verified against PostgreSQL 18.6 and pgvector 0.8.7 in the dedicated database configured through `set_db.bat`. Do not reuse the library metadata database. Copy `.env.example` to `.env` and set the search token, database URL, and a verified local embedding profile. The service stays unready until it has a database schema and tokenizer/model settings. `.env` is ignored by Git.

The project has no selected embedding model or approved language list. Supply a locally stored tokenizer file and the exact immutable model revision after checking its license and endpoint behavior. The model URL must use loopback HTTP in local mode. Cloud mode requires HTTPS and both `SEARCH_EMBEDDING_LOCATION=cloud` and `SEARCH_EMBEDDING_ALLOW_CLOUD=true`.

Install and run the owned schema migration from this directory:

```powershell
uv sync --locked
uv run --env-file .env --locked python -m search_service.migrate
.\scripts\run.ps1
```

The launcher reads `services/search/.env` automatically; values already present in the process environment take precedence.

The migration checks that pgvector is already installed and upgrades the search-owned schema transactionally. The runtime database user needs access to the `search` schema and tables. It does not need permission to install extensions. Search binds to loopback by default. Give the library `SEARCH_TOKEN` as its service credential; never expose it to the browser.

## Verify

```powershell
uv run --locked pytest -q
uv run --locked mypy src
uv run --locked ruff check src tests scripts
```

Use a disposable search database for migration, restart, deletion, and concurrency checks. A unit test with a fake embedding endpoint does not establish multilingual relevance or GPU capacity. Record real endpoint compatibility and evaluation evidence under `evidence/` before declaring those gates complete.

## Data and recovery

PostgreSQL stores immutable job snapshots, generated `simple` text vectors, exact pgvector embeddings, active revisions, and permanent deletion fences. Snapshots and segments contain sensitive slide and speaker-note text. Keep the search database private and back it up with the library metadata and originals under a coordinated stopped-service procedure. Restore the library's newest deletion fences before reopening queries. Search vectors can be rebuilt from current manifests, but a restored database must not be exposed until fence replay is complete.

Back up the search database with `pg_dump --format=custom --file=search.dump <database>` after stopping the library and search workers. Restore into a separate database with `pg_restore --dbname=<database> search.dump`, confirm the `vector` extension and schema version, then replay newer library deletion identities before connecting clients. Retain database WAL and backups under the site's separate retention policy.

## Limits

This milestone indexes visible native slide text and included notes. It does not index OCR text, captions, image content, or object geometry. The `simple` dictionary supports token matching without English stemming, not substring matching or guaranteed segmentation for every script. Retrieval quality and proposed latency limits remain unmeasured until the owner chooses languages and the local model is evaluated.
