# PostgreSQL text and vector slide search plan

Plan the text-search milestone for the shared-library team using the [feature design](../text-search-design.md).
Use a separate search-owned PostgreSQL database for full-text search and pgvector exact cosine matching, following sections 5 and 10 of the [preliminary design](../design_doc.md).
Combine lexical and semantic slide ranks with reciprocal rank fusion before grouping decks. Keep the library's existing database choices and ownership guards.
Rank decks by their best matching slide and support multilingual queries.
Keep originals, previews, notes policy, retry safety, and deletion guarantees intact.
Implementation was explicitly authorized on 2026-11-09 with `feature/text-search` as the intended branch. The verified commits are currently on `main` (`0fe52ca`, `6426f6f`); no PR was opened. No deployment, merge, or real library data migration is authorized.

## Current execution status

S1 has an independently startable PostgreSQL/pgvector service, durable indexing jobs, exact lexical and vector retrieval, profile registration, and idempotent migration. On 2026-11-14, the search migration/probe succeeded against the database configured in `services/search/.env`, followed by a PostgreSQL-backed live indexing and retrieval workflow.

S2 has durable library intents, status polling, a library-only gateway, retry recovery, generation filtering, and deletion handoff. S3 has grouped results, policy disclosure, degraded-search states, safe snippets, authenticated previews, and selected-slide gallery entry.

The latest continuation makes library search revisions independent from extraction source revisions and compare-and-set guards worker responses against changed or deleted intent state. The SQLite regression covers monotonic revision assignment, stale response rejection, and deletion-fence completion. This is groundwork only: the durable profile-reindex operation and search-side staged cohort/activation API are not implemented.

On 2026-11-14, search, library, extraction, and frontend checks passed. Initially, four library PostgreSQL tests skipped because no disposable test DSN was configured. The operator later designated the search `.env` database disposable. All 36 library tests then passed using the configured PostgreSQL server to create and drop per-test databases. A live search API run against `vectordb` indexed and queried two revisions for one synthetic deck at the same source revision, rejected a stale revision, deleted the deck, returned no result afterward, and rejected a later submission with the permanent fence. PostgreSQL checks confirmed the test slide payloads and segments were removed. This verifies current-profile database behavior, not profile reindexing or relevance quality.

The first synthetic, read-only library query returned lexical-only degraded results and zero hits. Diagnosis found the configured 5,000-second statement cap left only 100 ms for embeddings under the 10-second request deadline. After fixing the budget and restarting search, one authenticated synthetic request directly to search returned hybrid results, 2 groups, no warnings, and coverage of 78 eligible slides: 12 searchable, 66 blank, and 0 hidden. This verifies an embedding call and hybrid response on the search API, but does not verify live library routing after restart or relevance quality.

The timeout fix uses one monotonic deadline across embedding and search, caps a provisional 250 ms retrieval reserve independently of the statement maximum, and applies remaining-time limits to lock acquisition and each retrieval statement. A PostgreSQL cancellation after the overall deadline maps to retryable `504 search_timeout`; other database failures map to sanitized `503`. The search service's focused regression failed before the fix and now passes. Its live response is evidence of functionality, not a performance measurement. The user's local 5,000-second statement setting remains unchanged for non-query work.

The configured model is a cloud Qwen3 endpoint. The first synthetic library query fell back because of the query-budget defect, not a verified provider failure; one explicit post-fix synthetic search request returned hybrid results. Browser automation was unavailable.

Use English, Spanish, and Simplified Chinese as provisional evaluation languages. Use the proposed gates of recall@5 >= 0.85 and relevant first slide >= 0.80. These defaults follow the operator's direction to use judgment. They do not establish the team's language needs.

The administrator profile-reindex flow, evaluation and workflow scripts, held-out relevance results, 100-deck workflow, restore checks, performance measurements, browser review, and final independent review remain open. Profile activation has not been implemented or exercised. The configured search database is disposable, and the library tests can create isolated databases from its PostgreSQL server. Do not mark a milestone release-verified until its plan gates have evidence.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. Check a box only when its evidence exists, such as a test receipt, API response, screenshot, or commit SHA. The body is a how-to. The appendices record decisions and unresolved evidence.

Execution uses `skills/poteto-mode/playbooks/autopilot-stack.md` from the installed pstack plugin. S2 builds on S1; S3 builds on S2. The operator merges the reviewed stack bottom-up. All three changes stop at merge-ready.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [x] State this plan and protocol to the operator. Start implementation only on an explicit instruction to execute.
- [x] On that instruction, write the plan path, S1 then S2 then S3, the verification rule, operator merge authority, and the done condition into `notes.md` in the program's store at `.audit/text-search/`.
- [ ] Read the execution playbook, `skills/swarm/SKILL.md`, `skills/how/SKILL.md`, `skills/poteto-mode/playbooks/opening-a-pr.md`, and the feature design from the installed plugin or repository as applicable.
- [ ] For unattended execution, arm an hourly audit using an available scheduler. Codex has no native pstack loop here. If no scheduler exists, record that limitation and keep execution attended.
- [ ] At each audit, inspect live processes and evidence, repair drift, and post a short status message only for a new result, failure, or owner decision. Keep the decision trail under `.audit/text-search/`.
- [ ] On a hold request, stop writes and preserve receipts and current revisions.

### Spawn owners

- [ ] Assign S1, S2, and S3 owners in dependency order under the execution playbook. Each receives the constitution, feature design, full current brief, and file boundaries.
- [ ] Keep S1 inside search and its evidence, S2 inside library and its contracts, and S3 inside frontend plus evaluation and deployment documentation. Coordinate overlapping evidence files through one writer.
- [ ] Hold all three changes for operator review with screenshots and a video before merge.

### PR mechanics, for every PR

- [ ] Resolve the available forge. Use the runtime's PR tool, Origin when available, or `gh`. Record base and head SHAs. Do not open or push a PR during this planning task.
- [ ] Run affected checks before a PR-facing push, with hooks enabled. Run deslop before commit and no-comments before review.
- [ ] Open reviewable PRs using the execution playbook's parent topology. Triage automated findings against actual behavior.

### Verdict and merge, for every PR

- [ ] At each code-ready head, run the ten live scenarios below through the swarm skill within available concurrency. Include independent correctness and maintainability reviewers. A missing required independent reviewer leaves the verdict blocked.
- [ ] Check unit, live, performance, and operator review evidence at the recorded head. Record baseline defects separately. Reverify changed behavior after fixes.
- [ ] Stop at merge-ready. The operator lands S1, S2, and S3 in order. No deployment or real-data migration is part of this plan execution without its own explicit instruction.

### Boot recipe, for every live lane

- [ ] Use an isolated worktree at the reviewed head and private disposable data roots. Never use a live team library for destructive checks.
- [x] For S1, configure the disposable database from `services/search/.env`, verify PostgreSQL 18.6 and pgvector 0.8.7, and apply the search-owned migration. The search database is separate from library metadata.
- [ ] Start search from `services/search` and verify missing-extension and database-unavailable readiness errors; run those failure lanes with isolated settings.
- [x] Start the configured library, extraction, and search listeners and verify their health endpoints before service checks.
- [ ] Run extraction through its documented Linux or WSL2 workflow; native Windows rendering fails closed.
- [ ] For S3, run frontend `npm ci` and `npm run dev`. Drive the rendered app with available browser automation, including keyboard and responsive checks. Drive `/tests/gallery.html` and `/tests/search.html` as fixtures.
- [ ] For API lanes, use the new workflow scripts against actual HTTP listeners. Their `--self-check` modes validate fixtures only and cannot replace live evidence. Read tokens from environment variables.
- [ ] Save JSON receipts, timings, and screenshots under `.audit/text-search/<PR>/lane-<n>/`. For an API lane, capture its actual terminal response as a screenshot. Do not capture private notes or credentials.

## Build the independently startable search service (S1)

**Depends on.** None.

**Files.**

- [x] Create `services/search/README.md`, `API.md`, `.env.example`, `pyproject.toml`, and `uv.lock`.
- [x] Create `services/search/src/search_service/` with settings, boundary models, embedding adapter, segment builder, PostgreSQL store and migrations, durable worker, lexical and vector retrieval, slide fusion, HTTP app, and entry point.
- [x] Create `services/search/tests/` and sanitized unit fixtures.
- [ ] Create `scripts/check_search_workflow.py` and `scripts/evaluate_search.py`; neither script exists yet.
- [x] Record PostgreSQL/pgvector versions, migration, and live workflow results in `services/search/evidence/smoke.md`.
- [ ] Record query plans, held-out model compatibility, and resource measurements in search evidence.

**Build.**

- [x] Verify endpoint dimension compatibility through the migration probe and exercise PostgreSQL lexical/vector retrieval, revision replacement, stale-revision rejection, and deletion against the disposable database.
- [ ] Complete Appendix A language, tokenizer, model-license, query-plan, and resource probes; revise provisional budgets from measured evidence.
- [x] Implement records, profile configuration, bounded chunking, authenticated REST routes, generated `simple` `tsvector`/GIN retrieval, profile-isolated `vector(n)` embeddings, and exact `<=>` cosine retrieval.
- [x] Collapse each retrieval path to unique slides, combine ranks with deterministic equal-weight RRF, rank decks by their best slide, return score kinds, and label lexical fallback.
- [x] Persist keyed jobs and snapshots, atomically publish complete builds, supersede older revisions, bound retries, and permanently fence deleted decks.
- [x] Provide deterministic fake embedding behavior in tests and a configured real endpoint probe. The tokenizer file is available locally but is not tracked in this commit.
- [ ] Track/package the tokenizer artifact and record model license and multilingual compatibility evidence.
- [x] Enforce one active profile, independent startup, and actionable not-ready states.
- [ ] Add staged maintenance and atomic profile activation; the reindex API is not implemented.

**You see.**

- [x] A service started independently accepts snapshots and exposes progress; live PostgreSQL tests verify hybrid retrieval, current-revision replacement, and no results after deletion. The earlier library query also verified labelled lexical fallback.
- [ ] Hold an embedding request in flight during deletion and verify late work cannot republish lexical or vector results.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [x] Run `uv run --env-file .env --locked pytest -q`, mypy, and Ruff; run the migration and live PostgreSQL workflow for indexing, idempotency, same-source revision replacement, stale-revision rejection, retrieval, deletion cleanup, and permanent fencing.
- [ ] Complete the listed unit matrix for notes-only and duplicate channels, empty queries, retry exhaustion, cancellation/rollback and connection reuse, in-flight deletion, zero-hit fallback, and maintenance rejection.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on the configured `swarm workers` model at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Inspect trunk upload and browse plus the head search fixture. Record that trunk lacks search. Gate the new job-to-ranked-result workflow and unchanged existing service health. Save `s1-lane-1.png`. Pass when head returns the labelled deck and slide and existing service health remains unchanged.
- [ ] Lane 2. Submit native and notes text containing different answers, then query exact terms and paraphrases. Include conflicting lexical and vector ranks and repeated chunks. Save `s1-lane-2.png`. Pass when both paths resolve to labelled slides, RRF ordering is correct, and each slide contributes only once per path.
- [ ] Lane 3. Submit multilingual and cross-language fixture queries against the real candidate endpoint. Save `s1-lane-3.png`. Pass when the compatibility receipt records actual supported language behavior and evaluation scores without claiming owner approval.
- [ ] Lane 4. Supply blank and hidden slides, then query the ready build. Save `s1-lane-4.png`. Pass when blank and hidden content creates no vectors and zero searchable slides is explained.
- [ ] Lane 5. Drop a submission response, replay its request key, and restart during indexing. Save `s1-lane-5.png`. Pass when there is one job and one published segment set after recovery.
- [ ] Lane 6. Submit a newer index revision before an older delayed request completes. Save `s1-lane-6.png`. Pass when only the latest revision is active.
- [ ] Lane 7. Delete during a blocked embedding call, release it, then replay an unknown-UUID deletion. Save `s1-lane-7.png`. Pass when all derived text is removed and no late submission can publish.
- [ ] Lane 8. Exercise wrong tokens, oversized bodies, malformed vectors, and unexpected model identity. Save `s1-lane-8.png`. Pass when every invalid boundary is rejected without secrets or partial publication.
- [ ] Lane 9. Inject 429, timeout, 401, invalid upstream vectors, and failed local inference without enabling cloud. Save `s1-lane-9.png`. Pass when indexing retries only permitted failures, query failure returns explicitly degraded lexical results including zero-hit cases, and no cloud request occurs. Separately stop PostgreSQL and verify 503 rather than empty success.
- [ ] Lane 10. Change the configured profile and rebuild while querying and adding another deck. Save `s1-lane-10.png`. Pass when query returns a clear maintenance error until a coherent profile activates.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure lexical retrieval, exact vector retrieval, fusion and grouping, combined database-ranking p95, warm query p95, cold startup, full indexing time, and peak service, database, and GPU memory at 2,000 representative slides. Record actual segment counts, dimensions, database size, and connection use. Measure existing service health latency on trunk and head.
- [ ] Probe. Run `uv run --locked python scripts/check_search_workflow.py --base-url <loopback-search-url> --real-endpoint --capacity` after implementing its documented modes. Read bearer secrets from the environment. Interleave baseline and head health probes; isolate model and ranking timings.
- [ ] Baseline. Record trunk health latency first. Trunk has no search metric; record that absence. Compare lexical-only, vector-only, and fused retrieval with identical fixtures and query sets. Inspect PostgreSQL query plans and record cold and warm runs. Do not benchmark alternative stores as a prerequisite to the selected PostgreSQL design.
- [ ] Rule. Use the proposed 250 ms combined lexical, exact vector, fusion, and grouping p95, 3 second warm query p95, 10 second query deadline, and 30 minute full indexing gates from the design. Fail on GPU exhaustion or corruption. Existing health p95 must not exceed the larger of 250 ms or 1.5 times baseline. Revise provisional gates only with recorded evidence.

**Review gate.** The operator reviews before merge.

- [ ] Post representative screenshots and a 30 to 60 second video of the observable change in chat for the operator. Save them under `.audit/text-search/S1/review/`. Include terminal evidence for internal API behavior.
- [ ] Record the operator's review before merge.

**Merge.**

- [ ] Obtain a clean independent verdict at the current head, resolve blocking findings, and confirm the base is current under the execution playbook.
- [ ] Stop at merge-ready and hand this change to the operator for ordered landing.

## Connect library indexing and deletion (S2)

**Depends on.** S1.

**Files.**

- [x] Update library models, API, service, and store for additive status, durable search intents, revision fencing, and safe worker reconciliation; metadata remains in its existing JSON record format, so no schema migration was needed.
- [x] Provide the library-owned search HTTP adapter and update API/docs plus library and database tests.
- [x] Extend `services/library/evidence/checks.md` with SQLite and PostgreSQL verification receipts.
- [ ] Create `services/library/scripts/check_search_workflow.py`.

**Build.**

- [x] Add optional search status while preserving `extracted`, bounded search settings, and sanitized authenticated policy disclosure.
- [x] Atomically persist new search intents with manifest publication, export text over HTTP, poll jobs, and recover interrupted submissions.
- [ ] Backfill pre-existing extracted manifests on enablement; no backfill coordinator is implemented.
- [x] Add authenticated query, per-deck more-matches, and retry gateway actions with idempotent job status.
- [ ] Add the durable profile-reindex gateway action and operation schema.
- [x] Validate search results against current identity, generation, profile, and deletion state; preserve safe result metadata and refill filtered candidate windows.
- [x] Fence locally before deletion, cancel search work, and keep deletion pending through remote cleanup; test unknown deck fencing.
- [ ] Verify search intent/index-revision/deletion-state transfer and replay across both metadata backends and offline restore.

**You see.**

- [ ] An uploaded deck progresses from extraction to search-ready, an existing extracted deck backfills without rendering, and a deleted deck disappears from query results before remote cleanup finishes.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [x] Run library pytest, mypy, Ruff, database workflow self-check, and PostgreSQL integration tests against per-test databases created from the disposable server; 36 tests passed with no skips.
- [x] Cover core search client authorization, policy validation, status handling, retry, deletion fencing, stale response CAS, and sanitized service failures.
- [ ] Cover lost responses, reindex barriers, outages across the full gateway, backup/restore, and transfer of search state.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on the configured `swarm workers` model at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Upload, browse, and download an original on trunk and head, then query only on head. Save `s2-lane-1.png`. Pass when original hashes match on both revisions and only head adds correct search.
- [ ] Lane 2. Upload a new deck and poll extraction and indexing separately. Save `s2-lane-2.png`. Pass when the deck is search-ready only after the full correct build publishes.
- [ ] Lane 3. Enable search on an existing extracted library with notes and legacy manifests. Save `s2-lane-3.png`. Pass when current manifests backfill once without extraction calls and legacy notes stay unknown.
- [ ] Lane 4. Hold embedding responses while publishing a replacement extraction generation. Save `s2-lane-4.png`. Pass when no old-generation snippet or preview survives the current-generation check.
- [ ] Lane 5. Fail extraction replacement, then separately fail indexing replacement. Save `s2-lane-5.png`. Pass when failed extraction preserves the old valid search and failed indexing exposes a clear failure without stale hits.
- [ ] Lane 6. Delete a searchable deck while search is down, restart search, and await cleanup. Save `s2-lane-6.png`. Pass when hybrid and lexical-only hits disappear immediately and deletion completes only after both lexical and vector cleanup.
- [ ] Lane 7. Delay old index submission across reprocess and across unknown-UUID deletion. Save `s2-lane-7.png`. Pass when lower revisions and fenced UUIDs cannot republish.
- [ ] Lane 8. Restart the library after uncertain search submission and job completion responses. Save `s2-lane-8.png`. Pass when one durable job exists and status converges after recovery.
- [ ] Lane 9. Change profile and reconcile a concurrent upload plus deletion during maintenance. Save `s2-lane-9.png`. Pass when no mixed-profile query succeeds and no deleted deck enters the activation barrier.
- [ ] Lane 10. Transfer disposable library metadata between SQLite and PostgreSQL while search remains in its separate PostgreSQL database. Restore search with newer deletion fences. Save `s2-lane-10.png`. Pass when all intents and fences survive library transfer, search ownership guards hold, and deleted content stays inaccessible through both retrieval paths after restore.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure baseline upload acceptance, deck-status, original-download, and delete-fence p95, plus added search submission and result-validation time. Measure query-to-response latency separately with a real endpoint.
- [ ] Probe. Implement and run `uv run --locked python scripts/check_search_workflow.py --capacity` against the running library and search services. Run the same sanitized existing workflow at trunk and head in interleaved passes. Keep model timings separate from gateway timings.
- [ ] Baseline. Record trunk workflow latency and original checksums before starting head. Search is absent on trunk, so record the added gateway cost directly.
- [ ] Rule. Existing workflow p95 must stay within the larger of 250 ms or 1.5 times baseline, excluding streamed transfer duration. Proposed added gateway p95 is at most 250 ms and warm full query p95 at most 3 seconds. Any deleted or stale hit fails regardless of timing.

**Review gate.** The operator reviews before merge.

- [ ] Post representative screenshots and a 30 to 60 second video of the observable change in chat for the operator. Save them under `.audit/text-search/S2/review/`. Include terminal evidence for internal API behavior.
- [ ] Record the operator's review before merge.

**Merge.**

- [ ] Obtain a clean independent verdict at the current head, resolve blocking findings, and confirm the base is current under the execution playbook.
- [ ] Stop at merge-ready and hand this change to the operator for ordered landing.

## Deliver grouped multilingual slide search (S3)

**Depends on.** S2.

**Files.**

- [x] Implement the search client, UI, selected-slide gallery entry, and styles in the frontend modules.
- [x] Update client/gallery fixtures and frontend README; create `frontend/tests/search.html`.
- [ ] Add owner-labelled relevance fixtures and held-out evaluation receipts; only synthetic UI fixtures exist.
- [ ] Update tracked deployment documentation and orchestration after inspecting ownership. Preserve the existing untracked launch scripts.

**Build.**

- [x] Runtime-validate search, policy, job, and deck status responses; keep authentication in memory and suppress stale asynchronous responses.
- [x] Add submit-based grouped search, best-slide previews, channel-labelled snippets, more matches, coverage, and loading/empty/degraded/failure/maintenance messages.
- [x] Add selected-slide gallery entry with generation/slide validation while preserving download and gallery behavior.
- [x] Create `frontend/tests/search.html` to exercise the real search and gallery modules, including plain-text snippet rendering and exact-slide open.
- [ ] Run the browser fixture at narrow and wide sizes; verify keyboard-only use, disconnect races, and stale preview cleanup in browser automation.
- [ ] Collect owner-approved language/quality criteria and run held-out real-model and 100-deck evaluation.
- [x] Document independent startup, database provisioning, settings, cloud disclosure, lexical fallback, retry, backup, deletion, and restore.
- [ ] Document backfill and profile-maintenance operations after implementing them; run stopped-service restore verification.

**You see.**

- [ ] A multilingual query displays deck groups with their best slide first. Users open the precise slide, inspect more matches, and download the unchanged original.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [x] Run frontend `npm run check`, `npm test`, and `npm run build`; extend malformed-response and selected-slide unit cases.
- [ ] Exercise browser fixtures in browser automation against actual modules.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on the configured `swarm workers` model at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Upload, open the gallery, download the original, and delete on trunk and head. Search only on head. Save `s3-lane-1.png`. Pass when existing downloads match checksums, deletion converges, and head adds the intended search controls.
- [ ] Lane 2. Submit a paraphrase and inspect ordered deck groups and each first slide. Save `s3-lane-2.png`. Pass when each deck appears once with its highest scoring unique slide first.
- [ ] Lane 3. Expand more matches in a deck with duplicate chunk hits. Save `s3-lane-3.png`. Pass when the action returns only that deck's unique slides and preserves the query.
- [ ] Lane 4. Run owner-labelled same-language and cross-language queries on the actual model. Save `s3-lane-4.png`. Pass when held-out scores meet the owner-agreed criteria with per-language results recorded.
- [ ] Lane 5. Open the selected search slide, navigate its gallery, download original and graphic assets, then close using Escape. Save `s3-lane-5.png`. Pass when the intended slide opens and existing warning, download, and focus behavior passes.
- [ ] Lane 6. Query while indexing, with an empty library, and with an image-only deck. Save `s3-lane-6.png`. Pass when each case explains coverage without falsely claiming a fully searchable deck.
- [ ] Lane 7. Disconnect or submit a new query before an older request and preview load complete. Save `s3-lane-7.png`. Pass when no superseded result changes the current view or leaks an object URL.
- [ ] Lane 8. Reprocess or delete the selected deck between results and modal open. Save `s3-lane-8.png`. Pass when the browser refreshes or explains the stale selection and never shows deleted text.
- [ ] Lane 9. Inject failed inference with and without lexical hits, malformed response, expired token, database outage, and profile maintenance. Save `s3-lane-9.png`. Pass when lexical fallback is visibly degraded, unavailable states provide accessible recovery, and no service secrets appear.
- [ ] Lane 10. Use keyboard only at narrow and wide viewport sizes, including hostile snippet markup and the processing-location notice. Save `s3-lane-10.png`. Pass when controls are operable, focus is visible, text cannot execute, and the content disclosure is readable.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure baseline gallery open and original-download timing, head query-submit-to-visible-best-slide p95, and query-to-modal timing. Record preview load separately from embedding and ranking.
- [ ] Probe. Drive trunk and head browser workflows in alternating runs with the same sanitized decks and viewport. Save timings and screenshots. Run `uv run --locked python scripts/evaluate_search.py --held-out` from search against the actual configured model.
- [ ] Baseline. Record trunk gallery and download timing first. Trunk has no search interaction; separately record the added submit-to-result and submit-to-preview times on head.
- [ ] Rule. Proposed warm submit-to-results p95 is at most 3 seconds and best-preview p95 at most 5 seconds. Existing gallery p95 must stay within the larger of 500 ms or 1.5 times baseline. All quality and accessibility gates must pass; these proposed timings require hardware evidence.

**Review gate.** The operator reviews before merge.

- [ ] Post representative screenshots and a 30 to 60 second video of the observable change in chat for the operator. Save them under `.audit/text-search/S3/review/`. Include terminal evidence for internal API behavior.
- [ ] Record the operator's review before merge.

**Merge.**

- [ ] Obtain a clean independent verdict at the current head, resolve blocking findings, and confirm the base is current under the execution playbook.
- [ ] Stop at merge-ready and hand this change to the operator for ordered landing.

## Close the program

- [x] Check completed boxes against their evidence and record unsupported cases.
- [ ] Confirm the complete upload, index, multilingual query, precise-slide preview, original download, retry, reprocess, and delete workflow on the running local system.
- [ ] Obtain owner agreement on the language set and retrieval acceptance criteria before declaring quality sufficient.
- [ ] Deliver PR links, verification receipts, measurements, configuration instructions, and remaining limitations. Do not declare visual search complete.

## Appendix A. Prototype evidence

No runtime prototype, model call, benchmark, or UI sketch was executed during planning. No prototype branch, SHA, or screenshot exists. PostgreSQL full-text search and pgvector are selected by the owner. Query throughput, model compatibility, multilingual quality, and hardware fit remain unproven. S1 must establish those before S2 starts.

The read-only explorer confirmed the existing manifest fields, separate notes channel, durable library operations, PostgreSQL migration support, generation retirement, and gallery entry-point limitation. These are source observations, not runtime proof.

Before implementing S1, run a temporary compatibility client against a provisioned local multilingual model using sanitized inputs. Record request fields, response ordering, actual dimensions, prefixes, tokenizer counts, non-Latin input, per-item limits, batching limits, and model identity. Never assume the model behind a compatible URL follows OpenAI's model-specific limits.

Provision the selected local PostgreSQL and pgvector versions in a disposable search database. Apply search migrations and verify generated `simple` lexical documents, GIN indexes, profile dimensions, and relational constraints. Compare lexical-only, exact vector-only, and RRF results using identical representative queries and segment counts. Exercise phrases, punctuation, multilingual input, duplicate chunks, profile mismatch, and query-embedding failure. Inspect query plans and measure retrieval, fusion, memory, and database size. Keep the prototype branch, SHA, commands, configuration fingerprint, timings, and terminal screenshots. No prototype may access private library tables or production content. HNSW is deferred until measured exact-search limits justify its recall and operational costs.

The owner selected grouped deck results and multilingual support in this session. Specific languages and quality thresholds remain owner decisions before model selection and release acceptance. Continue contract and fixture work while collecting that decision.

## Appendix B. Alternatives rejected

A mandatory library-wide PostgreSQL migration adds data movement without a requirement. Existing library PostgreSQL support remains available. Search uses a separate PostgreSQL database to preserve the library's ownership checks. SQLite search persistence with application-side vector scoring is superseded by the owner's selection of PostgreSQL for lexical and vector matching.

Embedding inside extraction couples rendering to model availability. Embedding inside library makes a new backend responsibility inseparable from canonical storage. A cohesive search service preserves independent startup without creating a service per helper.

One vector for an entire deck loses slide attribution. One unbounded vector per slide silently loses long content. Bounded native and notes segments preserve source identity and allow notes-only matches.

A global top-chunk cutoff can fill the candidate set with one long deck. Collapse each path to unique slides, fuse slide ranks, and group by the best slide before limiting decks. Adding raw lexical and cosine scores is rejected because their scales differ.

Visual captions, OCR, approximate indexes, rerankers, cloud fallback, query caching, and selected-slide export remain outside this text-search increment. Hybrid lexical and vector retrieval with labelled lexical fallback is included. Adopt the preliminary design's retrieval approach without its tenant ACLs, broker, object storage, fixed model dimensions, or larger capacity target.

## Appendix C. Risks

S1 owns PostgreSQL and pgvector setup, migration integrity, bounded connections and statements, query plans, endpoint compatibility, pinned tokenizer provisioning, model licensing, dimensions, multilingual quality, fused ranking, and GPU fit. An OpenAI-compatible route does not establish any of those. The `simple` lexical configuration does not prove language-specific segmentation or cross-language quality. Fetch current dependency and candidate-model documentation through Context7 before implementation.

S2 owns durable publication, library SQLite and PostgreSQL transfer integrity, search PostgreSQL restore, deletion races across both retrieval paths, restore replay, profile maintenance, and temporary search gaps during generation replacement. Existing extraction retirement means old previews cannot be assumed available.

S3 owns accessible grouping, stale response suppression, precise-slide navigation, and disclosure. Search quality requires owner-labelled relevance and actual languages. No claim of visual retrieval or screenshot OCR is justified.

No project-specific browser driver skill was found. Use the available browser automation and preserve actual browser receipts. If a required independent review cannot run, leave its gate blocked. Performance thresholds in this plan are proposals, not measurements or service promises.

The runtime has no native hourly pstack loop. An unattended run needs a scheduler or an explicit attended alternative. No scheduler is armed by this planning task.

## Appendix D. Links and reading list

- [Constitution](../constitution.md), governing constraints and first-release visual search.
- [Preliminary design](../design_doc.md), representation, profile isolation, and evaluation ideas.
- [Feature design](../text-search-design.md), data shapes, settings, lifecycle, API, and UX decisions.
- [Library API](../../services/library/API.md) and [README](../../services/library/README.md), gateway, storage ownership, transfer, retry, and deletion.
- [Extraction API](../../services/powerpoint-extraction/API.md), current manifest identity, notes, and preview contract.
- [Frontend README](../../frontend/README.md), current gallery and verification commands.
- [Official embeddings API reference](https://developers.openai.com/api/reference/resources/embeddings/methods/create), retrieved through Context7 on 2026-11-09.
- [PostgreSQL text-search controls](https://www.postgresql.org/docs/current/textsearch-controls.html) and [pgvector documentation](https://github.com/pgvector/pgvector), retrieved through Context7 on 2026-11-09 for lexical ranking and exact cosine matching. No database or performance checks were run for this revision.
- Read `skills/how/SKILL.md` before S1 and S2 boundary changes. Use `skills/interrogate/SKILL.md` if storage, lifecycle, or profile design remains contested.
- During execution, follow `skills/show-me-your-work/SKILL.md` and retain the decision trail in `.audit/text-search/`.
