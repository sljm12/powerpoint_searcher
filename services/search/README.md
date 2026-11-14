# PowerPoint search service

This service owns text indexing, exact vector retrieval, lexical retrieval, and slide ranking. It does not read library files or tables. The library sends bounded slide text over HTTP and supplies current preview and download URLs.

## Configure and run

Use a dedicated PostgreSQL 18 database with pgvector 0.8.7 installed by a database administrator. This pairing was verified against PostgreSQL 18.6 and pgvector 0.8.7 in the dedicated database configured through `set_db.bat`. Do not reuse the library metadata database. Copy `.env.example` to `.env`; `.env` is ignored by Git. Values already present in the process environment take precedence over values in `.env`.

The service uses the OpenAI Python client against an OpenAI-compatible embeddings API. Before enabling cloud mode, account for native slide text, included speaker notes, and search queries being sent to the configured provider. Local mode requires a loopback HTTP endpoint. Cloud mode requires HTTPS and both `SEARCH_EMBEDDING_LOCATION=cloud` and `SEARCH_EMBEDDING_ALLOW_CLOUD=true`.

### Environment settings

“Required” means the service needs a meaningful value for that setting to run the relevant workflow. Defaults shown here are applied by the service when the variable is omitted or blank. The embedding worker becomes ready only when its endpoint, model, revision, expected dimensions, input limit, and tokenizer file are configured and the tokenizer file exists. Migration additionally probes the endpoint and checks the returned vector dimension before changing the database.

| Variable | Purpose | Mandatory? | Default / guidance |
| --- | --- | --- | --- |
| `SEARCH_HOST` | HTTP listen address. | No | `127.0.0.1`; must remain loopback. |
| `SEARCH_PORT` | HTTP listen port. | No | `8002`. |
| `SEARCH_TOKEN` | Shared service credential used by the library when calling search. | Yes for authenticated API use | Set a long random secret; do not expose it to the browser. |
| `SEARCH_DATABASE_URL` | PostgreSQL connection URL for the search-owned database. | Yes for search and migration | Use a dedicated database, not the library metadata database. |
| `SEARCH_DATABASE_CONNECT_TIMEOUT_SECONDS` | Maximum time to establish a database connection. | No | `5` seconds. |
| `SEARCH_DATABASE_STATEMENT_TIMEOUT_SECONDS` | Maximum PostgreSQL statement timeout for operations outside a search request. Search statements are capped by the remaining request deadline. | No | `5` seconds (converted to milliseconds). |
| `SEARCH_QUERY_TIMEOUT_SECONDS` | Overall query time budget, including embedding and retrieval. | No | `10` seconds. |
| `SEARCH_EMBEDDING_BASE_URL` | OpenAI-compatible API base URL; the client appends the embeddings route. | Yes for readiness and migration | Local mode: loopback HTTP. Cloud mode: HTTPS base URL, without credentials, query, fragment, or `/embeddings` suffix. |
| `SEARCH_EMBEDDING_API_KEY` | Bearer credential for the embedding endpoint. | Conditional | Required by authenticated providers such as OpenRouter; may be blank for a local endpoint that needs no key. |
| `SEARCH_EMBEDDING_MODEL` | Model identifier sent in embedding requests. | Yes for readiness and migration | Must be accepted by the endpoint. |
| `SEARCH_EMBEDDING_EXPECTED_MODEL` | Model identifier expected in the API response. | No | Blank uses `SEARCH_EMBEDDING_MODEL`. Set only to the exact response model name if the endpoint reports a different identifier. |
| `SEARCH_EMBEDDING_REVISION` | Immutable model revision included in the search profile identity. | Yes for readiness and migration | Use a pinned revision where the provider exposes one; changing it creates a different profile. |
| `SEARCH_EMBEDDING_EXPECTED_DIMENSIONS` | Expected vector length; validates endpoint responses and sets pgvector column size. | Yes for readiness and migration | Must match the endpoint response. Migration probes this before applying schema changes. Use digits only. |
| `SEARCH_EMBEDDING_REQUEST_DIMENSIONS` | Optional `dimensions` request argument to ask the provider for a particular output size. | No | Blank lets the provider use its default. If set, endpoint must support it and output must equal expected dimensions. |
| `SEARCH_EMBEDDING_QUERY_PREFIX` | Text prepended to each search query before tokenization and embedding. | No | Blank; set only if the selected model requires a query instruction. |
| `SEARCH_EMBEDDING_DOCUMENT_PREFIX` | Text prepended to slide text before tokenization and embedding. | No | Blank; set only if the selected model requires a document instruction. |
| `SEARCH_EMBEDDING_TOKENIZER_PATH` | Local tokenizer JSON used to count tokens and split slide text into chunks. | Yes for readiness | File must exist on the search service machine, including when embeddings are cloud-hosted. Use forward slashes in `.env` paths. |
| `SEARCH_EMBEDDING_MAX_INPUT_TOKENS` | Maximum tokens accepted by the selected model for one input. | Yes for readiness and migration | Set to the verified model/provider limit. |
| `SEARCH_EMBEDDING_CHUNK_TOKENS` | Maximum tokenizer tokens in each slide text chunk before adding the document prefix. | No | `256`; must be positive and no larger than the model input limit. |
| `SEARCH_EMBEDDING_CHUNK_OVERLAP` | Number of tokenizer tokens repeated between adjacent chunks. | No | `32`; must be smaller than chunk size. |
| `SEARCH_EMBEDDING_BATCH_SIZE` | Maximum number of inputs grouped into one embedding request. | No | `1`. |
| `SEARCH_EMBEDDING_MAX_BATCH_TOKENS` | Token budget used when grouping embedding inputs into a request. | No | `256`. |
| `SEARCH_EMBEDDING_TIMEOUT_SECONDS` | Timeout for each embedding API request. | No | `30` seconds. |
| `SEARCH_EMBEDDING_MAX_ATTEMPTS` | Maximum attempts for retryable indexing requests. Queries are attempted once. | No | `3`. |
| `SEARCH_EMBEDDING_MAX_RESPONSE_BYTES` | Maximum accepted embedding API response body size. | No | `8388608` bytes (8 MiB). |
| `SEARCH_EMBEDDING_LOCATION` | Declares whether embedding processing uses a local or cloud endpoint. | No | `local`; allowed values are `local` and `cloud`. |
| `SEARCH_EMBEDDING_ALLOW_CLOUD` | Explicit administrator opt-in required for cloud embedding mode. | Conditional | `false`; set `true` only together with `SEARCH_EMBEDDING_LOCATION=cloud`. |

Install and run the owned schema migration from this directory:

```powershell
uv sync --locked
uv run --env-file .env --locked python -m search_service.migrate
.\scripts\run.ps1
```

The launcher reads `services/search/.env` automatically; values already present in the process environment take precedence.

Check the configured embedding endpoint and returned vector dimension before migrating the database:

```powershell
uv run --env-file .env --locked python scripts/test_openrouter_embeddings.py
```

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
