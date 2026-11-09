# PostgreSQL text and vector slide search design

Status: proposed, revised 2026-11-09 to select PostgreSQL full-text search and pgvector. Implementation has not started.

This feature helps the shared-library team find relevant slides using a natural-language query. Results rank decks by their best matching slide. Each deck group opens with that slide and offers more matching slides from the same deck. The project owner selected multilingual search and this grouping on 2026-11-09.

## Scope and governing rules

The [constitution](constitution.md) takes precedence. This design uses [the preliminary design](design_doc.md), particularly sections 5, 10, 13, and 15, as provisional guidance.

This is a text-search milestone toward the full release. Index extracted slide text and included speaker notes. Do not declare the constitution's visual-search requirement complete. Text absent from extraction, including words inside screenshots, and visual meaning without native text remain unsupported here. Existing previews and downloads remain part of the search workflow.

Use the constitution's initial 100-deck, approximately 2,000-slide target. Adopt the preliminary design's PostgreSQL full-text search, pgvector, per-slide collapse, reciprocal rank fusion, and explicit lexical fallback. Its 100,000 slides, English-first assumption, tenant permissions, object storage, queue broker, OCR, visual descriptions, and reranker are outside this increment. Multilingual and cross-language retrieval require evaluation; Unicode support alone does not establish either.

No constitution amendment is needed. Local processing remains the default. Cloud processing requires explicit administrator configuration and disclosure of the content sent.

## Service and storage ownership

Add one independently startable Python service at `services/search/`. It owns text segments, vectors, immutable embedding profiles, durable jobs, active index builds, and deletion fences. Its dependencies, migrations, tests, README, API contract, and `uv.lock` live there. Keep HTTP handlers, embedding calls, and retrieval logic separate using the existing service patterns.

The library remains the frontend's only backend. It owns canonical deck identity, extraction manifests, originals, access control, and orchestration. It sends bounded text snapshots to search over authenticated REST, then polls indexing jobs. Bind each search database to one library UUID on first initialization and reject a different library thereafter. Search never reads library tables, private files, extraction storage, or asset URLs. No shared business-logic package is introduced. The frontend never receives embedding credentials or the search service token.

Keep the existing library SQLite and PostgreSQL choices. The owner selected local PostgreSQL for search metadata, jobs, lexical indexes, and vectors on 2026-11-09. Enable pgvector in a separate search-owned database on the same local server. Library PostgreSQL startup currently rejects unrelated user tables in its database. Preserve that guard and keep search migrations and credentials separate. Neither service reads the other's tables.

Use a stored `tsvector` generated from segment text with the explicit `simple` configuration and a GIN index. Store embeddings in pgvector `vector(n)`, where `n` is the verified profile dimension, with foreign keys tying each embedding to its segment, build, and profile. Do not copy the preliminary design's 768 dimensions or selected model as defaults. Use database constraints for segment uniqueness, keyed submissions, library binding, and profile consistency.

Start with exact cosine distance in PostgreSQL using pgvector's `<=>` operator. No HNSW or IVFFlat index is required at the initial capacity. Measure the complete filtered query before considering approximate indexes. Any later ANN change needs recall comparison against exact results and an exact fallback for underfilled filtered searches. A dimension change needs a search-owned embedding-table migration before rebuilding and activating the new profile.

Provision the extension through an administrator setup step. Run the service with credentials restricted to its database and no extension-installation privilege. Pin compatible PostgreSQL and pgvector versions during S1. Search representations are rebuildable from manifests, but jobs, profiles, library binding, and permanent deletion fences must survive backup and restore. There is no existing search database to migrate. A future store migration must preserve those records. Library metadata transfer remains an independent operation.

## Search records and input composition

| Record | Identity and content |
| --- | --- |
| Embedding profile | Stable fingerprint of endpoint identity, declared model revision, expected dimensions, tokenizer fingerprint, prefixes, normalization, and chunk-format revision. Exclude credentials. |
| Deck build | Library UUID, deck UUID, source revision, extraction generation UUID, index build UUID, monotonic index revision, profile ID, captured notes policy, state, and progress. |
| Segment | Build UUID, slide number, channel of `native` or `notes`, ordinal, source references and offsets, text, generated lexical `tsvector`, and exact embedding-input hash. |
| Embedding | Segment and build identity, profile ID, and validated pgvector `vector(n)`. |
| Job | Job UUID, request key and payload hash, build identity, attempt, state, progress, safe error, and timestamps. |
| Deletion fence | Library UUID and deck UUID. Permanent identity only, without slide text or filenames. |

Unique constraints prevent repeated segments and repeated keyed submissions. Job states are `queued`, `processing`, `ready`, `failed`, and `cancelled`. A build becomes active in one PostgreSQL transaction after every eligible slide has been accounted for and both lexical documents and embeddings are complete. Both retrieval paths use the same active-build pointer. No partial build is searchable.

Read native text in slide and occurrence order. Use `text_blocks` when present, otherwise `paragraphs`, otherwise `text`. Never concatenate these equivalent representations. Exclude inherited master and layout boilerplate, and do not treat alternative text as visible slide text. Preserve table row order and source references. Retain original Unicode for display, normalize Unicode and whitespace consistently for embedding inputs, and version that normalization. Never deduplicate equal text across different slides.

Keep notes in their own channel. Include only `notes.state=present`; preserve the extraction truncation warning. Legacy null notes mean unknown, and excluded notes stay excluded. Do not fetch missing notes from originals. Reprocess through the existing library route to recover notes under the current policy. A notes-policy configuration change applies to new extraction operations as today; it does not silently rewrite old manifests.

Split paragraphs and table row groups into bounded token windows. The profile supplies a pinned tokenizer matching the served model, query and document prefixes, maximum input tokens, chunk tokens, and overlap. Account for prefixes in the token budget. Split long paragraphs without dropping text. Reject an invalid profile or an oversized query with an actionable error; do not rely on endpoint truncation. Do not assume character counts equal token counts across languages. Provision tokenizer files locally and disallow automatic network downloads at runtime.

Preserve hidden-slide identity but exclude hidden slides from search in this increment. Do not embed blank channels. An image-only deck can complete indexing with zero searchable slides and a visible explanation. Report eligible, searchable, blank, and hidden counts so zero results do not imply a broken index.

## Embedding API configuration

Use the existing Python HTTP client pattern rather than adding an SDK wrapper. Required settings have no invented model defaults. An unconfigured search service reports not ready while upload, extraction, browsing, and original download remain usable.

| Setting | Meaning and initial policy |
| --- | --- |
| `SEARCH_DATABASE_URL` | Required connection secret for the separate search-owned PostgreSQL database with pgvector installed. Never disclose or log it. |
| `SEARCH_DATABASE_CONNECT_TIMEOUT_SECONDS`, `SEARCH_DATABASE_STATEMENT_TIMEOUT_SECONDS` | Bounded connection and statement waits. Query statements also respect the remaining end-to-end deadline. |
| `SEARCH_HOST`, `SEARCH_PORT` | Loopback listener by default, proposed port 8002. |
| `SEARCH_TOKEN` | Service-to-service bearer secret. Never a browser setting. |
| `LIBRARY_SEARCH_URL`, `LIBRARY_SEARCH_TOKEN` | Library-side search endpoint and matching secret. |
| `SEARCH_EMBEDDING_BASE_URL` | Required URL ending at the API base, for example `http://127.0.0.1:8080/v1`. Append `embeddings` exactly once. |
| `SEARCH_EMBEDDING_API_KEY` | Optional bearer key for local endpoints that require it; required when the configured cloud provider requires it. |
| `SEARCH_EMBEDDING_MODEL` | Required model name accepted by the endpoint. |
| `SEARCH_EMBEDDING_REVISION` | Required administrator-declared immutable served-model revision. A mutable alias cannot prove model identity. |
| `SEARCH_EMBEDDING_EXPECTED_DIMENSIONS` | Required positive response-vector length. |
| `SEARCH_EMBEDDING_REQUEST_DIMENSIONS` | Optional request field. Omit by default; configure only after compatibility verification. |
| `SEARCH_EMBEDDING_QUERY_PREFIX`, `SEARCH_EMBEDDING_DOCUMENT_PREFIX` | Explicit model-specific strings, empty by default. |
| `SEARCH_EMBEDDING_TOKENIZER_PATH` | Required local pinned tokenizer artifact. Fingerprint its bytes. |
| `SEARCH_EMBEDDING_MAX_INPUT_TOKENS` | Required verified per-input limit. |
| `SEARCH_EMBEDDING_CHUNK_TOKENS`, `SEARCH_EMBEDDING_CHUNK_OVERLAP` | Required valid window size and smaller nonnegative overlap. |
| `SEARCH_EMBEDDING_BATCH_SIZE`, `SEARCH_EMBEDDING_MAX_BATCH_TOKENS` | Bound both items and summed tokens; start with one item until the endpoint passes batching checks. |
| `SEARCH_EMBEDDING_TIMEOUT_SECONDS` | Proposed total request deadline of 30 seconds, including bounded connection and read waits. |
| `SEARCH_QUERY_TIMEOUT_SECONDS` | Proposed end-to-end search deadline of 10 seconds. Query embedding and candidate retrieval use the remaining deadline, even when the indexing request timeout is longer. |
| `SEARCH_EMBEDDING_MAX_ATTEMPTS` | Proposed three indexing attempts with capped exponential backoff and jitter. Query requests do not retry automatically. |
| `SEARCH_EMBEDDING_MAX_RESPONSE_BYTES` | Bound streamed response size, provisionally 8 MiB. Reject profiles whose maximum batch cannot fit. |
| `SEARCH_EMBEDDING_LOCATION` | `local` by default; `cloud` requires explicit configuration. |
| `SEARCH_EMBEDDING_ALLOW_CLOUD` | False by default. Must be true alongside cloud location before sending content there. |

Provide a committed `.env.example` with placeholders and a documented local profile. Select and license-check the actual multilingual model during the compatibility probe. The preliminary design's multilingual E5 model is a candidate to test, not an already verified choice. Fetch current model-card and tokenizer documentation before selecting it.

In local mode, initially permit only loopback model endpoints. Remote or LAN hosting needs a documented explicit policy extension rather than silent cloud classification. Reject credentials in URLs, query strings, and fragments. Disable redirects and proxy-environment inheritance, and verify TLS for cloud endpoints. A location label alone must not bypass address checks.

Send `POST {base_url}/embeddings` with JSON containing `model`, a nonempty string-array `input`, and `encoding_format="float"`. Send `dimensions` only when explicitly configured. Do not send a chat conversation, query instructions from slides, end-user identifiers, or arbitrary provider-specific fields.

The [official OpenAI embeddings reference](https://developers.openai.com/api/reference/resources/embeddings/methods/create), retrieved through Context7 on 2026-11-09, documents batched string inputs, optional dimensions, and indexed vectors in `data`. These fields define the interoperability target. OpenAI's model limits and dimensions support do not establish a local provider's capabilities.

Validate HTTP status, bounded JSON, item count, unique contiguous `data[].index`, numeric finite nonzero vectors, and configured dimension. Reorder vectors using the indices rather than response order. Reject booleans as vector numbers. Normalize vectors locally before persistence and query comparison. Permit irrelevant extra response fields; never require a particular usage-reporting extension. Record the returned model identifier during the probe and define an explicit expected alias if it differs from the requested model. Reject an unexpected model identity thereafter.

Retry indexing only for connection failures, timeouts, 429, and transient 5xx responses. Honor bounded `Retry-After`. Invalid credentials, incompatible schema, bad dimensions, unknown models, and invalid inputs fail with a safe configuration or content error. A repeated embedding call can incur cloud cost, but only one durable build can publish. Do not store raw upstream error bodies or log queries, notes, input text, credentials, or endpoint secrets.

Expose sanitized model name, profile ID, readiness, processing location, and a disclosure through the authenticated library policy endpoint. Before upload and search, explain that native slide text, included notes, and query text go to the configured model service. Images and original `.pptx` bytes do not go to the embedding endpoint. Index only newly authorized content after configuration changes; never fall back from a failed local model to cloud.

## Indexing, replacement, and deletion

The library keeps `Deck.state=extracted` with its existing meaning. Add a separate optional `search` status object with index revision, generation, profile, job ID, progress counts, warnings, and safe error. Distinguish `not_configured`, `queued`, `processing`, `ready`, `failed`, and `deleting`. Never label an extracted deck search-ready until its complete current build is published.

In the transaction that publishes an extraction manifest, enqueue a durable search intent in library-owned metadata. Persist the intended payload identity and monotonic per-deck index revision before HTTP submission. The library exports only bounded native and notes content, slide identities, and provenance needed for search. Search stores no original bytes or images. Persisted input snapshots are sensitive derived data and are deleted with the deck.

Use a stable request key for submission and polling recovery. A higher index revision supersedes a lower revision, including when old HTTP submissions arrive late. Search rejects equal revision with different payload. After restart, resume jobs from durable state and reuse completed batch rows within the same build. Do not compare or reuse vectors under a different profile.

On first enablement, enqueue current extracted manifests through a resumable authenticated reindex action or the startup reconciler. Do not rerender existing decks to create text vectors. Reconcile missing intents after upgrade and restart. Record per-deck failures without blocking other decks. Stop or yield between bounded batches so query embedding requests can take priority on a single local model endpoint.

When extraction publishes a replacement, the library immediately stops exposing hits for the previous generation. Existing extraction can retire its old assets before replacement embeddings are ready. Accept a temporary search gap for that deck rather than returning stale preview links. A failed extraction replacement leaves the previous manifest and compatible index intact. A failed indexing replacement reports failure and exposes no stale-generation hits.

For a model-profile change, use an explicit administrator reindex operation. Stop query service with `reindex_required` while the new corpus profile builds, then atomically activate it after every current eligible deck is ready or deliberately excluded with a recorded failure. Do not automatically exclude failures or mix profiles. Uploads and deletion must join that reconciliation barrier. This first version accepts a disclosed search maintenance window; it does not keep two model servers loaded or add live dual-profile routing.

Deletion commits the library fence first and hides the deck from search immediately. In the same durable transaction, cancel search intents and queue search deletion. Search commits its permanent fence before acknowledging cleanup, cancels jobs, removes segments, lexical index entries, vectors, staged inputs, and snippets, and rejects late submissions even for unknown UUIDs. Library deletion completes only after originals, extraction, and search cleanup finish. An unavailable search service leaves deletion pending and hidden, with indefinite bounded-backoff cleanup retries.

Before returning a search response, the library validates every result against live deck identity, current generation, index revision, active profile, and deletion fence. Fetch additional ranked candidates when filtering would underfill a page. Never leak stale text through snippets, totals, or counts. As with existing downloads, an already authorized response may finish transmission after deletion begins.

## REST interfaces and result behavior

The search service API is internal and bearer-authenticated except minimal liveness. Every contract validates size, UUIDs, enums, and identities at the boundary.

| Route | Request and successful response |
| --- | --- |
| `GET /health` | Minimal liveness and readiness, without credentials or provider URLs. |
| `POST /v1/index-jobs` | `Idempotency-Key` plus library, deck, extraction generation, source revision, index revision, profile, and bounded slide text snapshot. Return 202 with `job_id` and `status_url`. Same key and input returns the same job; changed input returns 409. |
| `GET /v1/index-jobs/{job_id}` | Return 200 with state, build identity, progress, warnings, and safe error. |
| `POST /v1/index-jobs/{job_id}/retry` | Keyed retry of a current retryable failed job. Return 202. Preserve profile, snapshot, and revision. |
| `POST /v1/search` | Query, active profile, optional deck UUID, hidden=false, and bounded candidate window. Return ranked deck groups or ranked slides for that deck. Include build identities and a deterministic continuation within this response computation. |
| `DELETE /v1/decks/{deck_id}` | Idempotent library-scoped deletion. Return 200 only after fence and derivative cleanup are durable. |

The public gateway adds authenticated `POST /v1/search` with `query`, optional `deck_id`, and `limit`. Default to 20 decks and allow 1 to 100. With `deck_id`, interpret the limit as slides within that one deck and default to 20 slides. Limit query text to 2,000 Unicode code points and the configured model token limit. Do not put queries in URLs or access logs. No persistent query cache or browser storage is needed.

Return `query_id`, `profile_id`, `mode` of `hybrid` or `lexical`, `degraded`, safe warnings, indexing coverage, and `decks`. Each deck group has `deck_id`, `filename`, ordered `matches`, and `has_more_matches`. A normal query returns one best slide in each group's `matches`; a deck-specific query returns up to the requested number of ranked unique slides in that one group. Each slide match has one-based `slide_number`, `generation_id`, index revision, ranking `score`, `score_kind` of `rrf` or `lexical`, matched retrieval paths, matched channel, a bounded plain-text snippet, preview asset identity, and warning metadata. The library supplies authenticated generation-bound preview and original URLs from the current manifest; never trust remote asset URLs supplied by search. Native and notes snippets identify their channel. Scores are ranking signals, not confidence percentages.

### Lexical and vector ranking

Use the same eligible set in both database queries: the bound library, current active build, active profile, nondeleted deck, visible slide, and optional requested deck. Run both paths and fusion against one consistent database snapshot. Bind query values as parameters. The library still checks canonical identities before returning results.

1. Build the lexical query with `websearch_to_tsquery('simple', query)`. Match the stored `tsvector` with `@@` and rank matching segments with `ts_rank_cd` descending. Use `simple` for both document and query processing. Skip lexical retrieval if parsing yields no positive searchable terms, including punctuation-only or negation-only input. Never turn that case into a full-corpus lexical match.
2. Embed the query with the active profile. Rank eligible embeddings by cosine distance `embedding <=> query_vector` ascending. Validate finite nonzero vectors and profile dimensions before SQL execution. Exact matching needs no approximate vector index.
3. Within each retrieval path, collapse segments to their best match per slide before assigning one-based slide ranks. Native text and notes share each path, so repeated chunks or both channels cannot give a slide extra votes. Use slide identity and segment ordinal to break ties deterministically.
4. Fuse the two slide rankings with `RRF(slide) = sum(1 / (60 + rank_path))`. A missing path contributes zero. Keep lexical and cosine scores separate. Neither raw-score addition nor treating the fused score as cosine similarity is valid.
5. Rank each deck by its highest-scoring slide. Sort by score descending, then deck UUID and slide number. A deck-specific request returns unique slides in that order. At this capacity, rank all eligible slides before limiting deck groups, rather than cutting off a global chunk list. Return a bounded response and deterministic continuation for library-side stale-result filtering.

Use the best lexical segment as snippet evidence when the slide has a lexical match, otherwise use its best semantic segment. Label native and notes evidence. Return nearest semantic matches without an arbitrary similarity threshold and evaluate unrelated queries before adding one. Empty corpus returns 200 and no results.

Full-text search is token-based, not substring search. `ts_rank_cd` is not BM25. The `simple` configuration avoids English stemming but does not establish word segmentation or relevance for every script. Evaluate owner-selected languages and punctuation-heavy identifiers explicitly. Language-specific dictionaries or identifier matching need evidence before a later design change. Web-search operators affect only the lexical path; this API does not promise Boolean filtering of semantic matches.

If query embedding is unavailable after a complete corpus has activated, return lexical results with `mode=lexical`, `degraded=true`, and an actionable warning. Use `score_kind=lexical` in this mode. No lexical hits returns a labelled degraded empty result, not a claim that semantic search found nothing. Reserve time within the query deadline for lexical retrieval and response assembly so an inference timeout cannot consume the entire fallback budget. Do not retry query inference automatically. Invalid upstream vectors or unexpected model identity also trigger a safe lexical fallback and a configuration warning. Never activate an incomplete build to provide fallback.

An unconfigured service or profile-maintenance barrier remains unavailable. Lexical fallback does not bypass either condition. Database failure, including lexical query failure, returns 503. Do not silently return vector-only results after a database query failure.

Invalid query or fields return 400; missing or incorrect authorization returns 401; missing resources return 404; conflicting keys, profiles, or stale revisions return 409; fenced deck submissions return 410; oversized input returns 413; unavailable storage, unconfigured search, or profile maintenance returns 503. Query-model failure uses the labelled lexical fallback above. Processing failures appear in job status with actionable, sanitized errors. Reuse the library's safe error envelope and request IDs. Specify timeouts and response bounds on every library-to-search call. Extend the owning API contracts and contract tests in the same PR as implementation.

## Browser interaction

Add a labelled search field and submit button above the library. Search on submit, with loading, empty-library, no-text, indexing-in-progress, degraded lexical-only, failed-provider, and reindex-maintenance messages. Preserve the query while the user browses a result. Reject stale responses when a newer query or disconnect supersedes them.

Show deck groups with the best slide's preview, source filename, slide number, matched-channel snippet, and original download. A "More matches in this deck" action requests unique ranked slides for that query and deck. Reuse the gallery modal through an optional selected-slide entry point. Validate the manifest generation before opening the requested slide; refresh on a stale selection. Render snippets as text, never HTML. Keep score values out of the primary UI. Display a persistent lexical-only warning when semantic matching is unavailable, including when there are no lexical hits.

Use keyboard-operable controls, visible focus, accessible names, a live status region, and focus restoration on modal close. Reuse gallery image cleanup, authenticated blob downloads, and crop-warning behavior. Model settings remain server-side. Show sanitized processing location and content disclosure in the search view.

## Evaluation and operations

Compare lexical-only, vector-only, and fused retrieval on the same held-out queries. Include exact terms, quoted phrases, punctuation-only input, identifiers, lexical misses with semantic hits, conflicting path ranks, and explicit model-outage fallback. Maintain a sanitized fixture corpus with known relevant slide numbers. Include paraphrases, notes-only answers, duplicate text, empty and hidden slides, long notes, table text, an unrelated query, same-language queries in multiple languages, and cross-language queries. Ask the owner which actual languages and scripts matter before selecting the model or declaring quality sufficient. Translation is not part of this feature.

Propose at least 30 labelled queries across at least three owner-selected languages, including ten cross-language queries. Track relevant-slide recall at 5 deck groups and reciprocal rank of the first relevant deck. For each returned deck, also assess whether its first slide is relevant. Proposed quality gates are recall at 5 of at least 0.85 and a relevant best slide for at least 0.80 of labelled relevant deck-query pairs. These are proposals requiring owner agreement, not measured results. Keep tuning queries separate from held-out queries. Retain visual queries as explicit expected unsupported cases for this milestone.

Measure 100 representative decks with real segment counts on the target GPU of at least 8 GB VRAM. Record cold model startup, peak GPU, service and database memory, indexing duration, query embedding time, lexical retrieval time, exact vector retrieval time, fusion and grouping time, end-to-end query latency, and upload/download behavior during indexing. Proposed engineering gates are warm p95 query latency at most 3 seconds, combined lexical retrieval, exact vector retrieval, fusion, and grouping p95 at most 250 milliseconds, query deadline at most 10 seconds, and full-corpus indexing at most 30 minutes. These are provisional gates to revise with measured evidence and owner agreement, not product promises. Record concurrency at one query and a four-query burst. Never compare a fake embedding endpoint's speed to a real model's speed.

Run one search service process and one durable indexing worker per search-owned database. PostgreSQL with pgvector is a required local dependency. No broker or managed database is required. Fail readiness with a safe actionable error for missing database access, extension, or schema. Apply search-owned migrations before startup and bound connection use and statement timeouts. Models load outside application services through the configured endpoint. Verify actual endpoint batching, optional dimensions, Unicode, response ordering, token handling, model identity, and resource use before release.

Back up library metadata and originals, extraction storage, the search-owned PostgreSQL database including jobs and profiles, and the newest deletion identities under a coordinated stopped-service procedure. Search vectors can be rebuilt, but restored content must remain unavailable until newer deletion fences are replayed. Document PostgreSQL backup and restore commands, extension and schema versions, vacuum, WAL, and backup retention separately from active deletion. Restore into a separate search database and verify library binding, active builds, lexical and vector results, and replayed deletion fences before reopening queries. A rebuild must not reintroduce notes removed by a newer manifest. Update deployment instructions and local orchestration only after their current tracked ownership is established; do not overwrite untracked launch scripts.

Implementation tasks and evidence gates are in the [text-search plan](plans/text-search.md).

## Documentation verification

PostgreSQL text-search controls and pgvector exact cosine retrieval were checked through Context7 on 2026-11-09. These references support the proposed database operations, not runtime or quality claims. Resolve documentation for the pinned versions again during implementation.

- [PostgreSQL text-search controls](https://www.postgresql.org/docs/current/textsearch-controls.html).
- [PostgreSQL text-search tables and indexes](https://www.postgresql.org/docs/current/textsearch-tables.html).
- [pgvector exact search, cosine distance, and approximate indexes](https://github.com/pgvector/pgvector).
