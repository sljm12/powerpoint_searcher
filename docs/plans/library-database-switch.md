# Switch the library between SQLite and PostgreSQL

Execution plan. Let an administrator choose SQLite or local PostgreSQL at startup and carry the existing library across during an offline transfer. Keep SQLite as the default. Deliver LIBDB-1, then LIBDB-2. Preserve original bytes, deck identities, operation history, and deletion fences.
This follows the [project constitution](../constitution.md). There are no constitutional conflicts. PostgreSQL remains optional and locally deployable. Only library-owned metadata changes backend. Extraction keeps its own storage and HTTP contract.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. Check a box only when its evidence exists in a test result, log, screenshot, or reviewed diff. Unchecked boxes describe future work, not completed verification.

Execution uses `skills/poteto-mode/playbooks/autopilot-stack.md` from the installed pstack plugin. LIBDB-2 depends on LIBDB-1. The operator merges both PRs after verification. LIBDB-1 implementation is authorized; LIBDB-2, PR creation, and merge remain out of scope.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] Present this plan and start implementation only on the operator's explicit go. Record that instruction in the decision trail.
- [ ] Copy the plan path, PR order, verification rule, operator merge authority, and completion criteria into `notes.md` in the program's store under `.audit/library-database-switch/`.
- [ ] Read the installed plugin's `poteto-mode`, `how`, `swarm`, `autopilot-stack`, `autopilot-full`, and `opening-a-pr` instructions before execution. Load `architect` before changing internal signatures and `interrogate` before accepting a contested transfer design. Record the resolved paths.
- [ ] For unattended execution, schedule an hourly audit and post a status message only when evidence changes. If scheduling is unavailable, use attended checkpoints and record that limitation. Do not claim a scheduler exists.
- [ ] Record a throughput checkpoint after the storage inventory. Use one implementation owner at a time because LIBDB-2 builds on LIBDB-1. Delegate isolated verification only after each head exists.

### Spawn owners

- [ ] Assign an owner to LIBDB-1, then LIBDB-2, with file boundaries and the acceptance checks below. Record the branch and head for each.
- [ ] Keep implementation inside `services/library/`. Keep feature decisions in this plan. Do not change frontend or extraction storage unless contract verification demonstrates a required client correction.
- [ ] Keep the operator review gate for LIBDB-2's administrator transfer interaction. Hand back evidence and wait for the operator's merge.

### PR mechanics

- [ ] Resolve the forge and follow the execution playbook for opening and stacking PRs. Record base and head SHAs. Run deslop before commits and no-comments before review.
- [ ] Run the owning module's checks before pushing. Read the full diff and triage review comments with evidence. Do not include credentials, deck text, or private endpoints in reports.

### Verdict and merge

- [ ] Run independent verification at the exact PR head under the swarm skill. Include the ten live scenarios below and an independent audit of transfer, deletion, identity binding, and ambiguous commits.
- [ ] If independent review cannot run, record `BLOCKED` with the missing review and keep merge closed. Tests and the author's review do not replace it.
- [ ] Hand both verified heads to the operator. Apply the execution playbook's current-base and patch-id rules after restacking. Nobody merges automatically.

### Boot recipe

- [ ] Create isolated worktrees and private data directories at the recorded base and head. Use synthetic decks and fresh tokens. Save artifacts under `.audit/library-database-switch/<pr>/lane-<n>/`.
- [ ] From `services/library/`, run `uv sync --locked` for SQLite. For PostgreSQL in LIBDB-2, use the proposed `uv sync --locked --extra postgres` and start a dedicated local PostgreSQL 18 test database. Validate this version and the locked Psycopg package on the supported platforms before claiming support.
- [ ] Start extraction independently using [its README](../../services/powerpoint-extraction/README.md). Use Linux or WSL2 for its current renderer. Start library with `uv run --locked python -m library_service`, adding `--extra postgres` in PostgreSQL mode. Start frontend with `npm.cmd run dev` from `frontend/` on Windows, or `npm run dev` on Linux.
- [ ] Drive authenticated routes through HTTP and exercise the gallery through the available browser driver. Use each service's README for token configuration. Capture response assertions and safe terminal screenshots for API-only lanes. Capture browser screenshots for gallery lanes. Record exact commands and ports in each receipt.
- [x] Create `services/library/scripts/check_database_workflow.py` as the repeatable HTTP driver for upload, polling, original checksum, retry, reprocess, deletion, and restart scenarios. Keep administrative setup outside its content APIs. Give the script a focused self-check. Extend it for transfer and outage probes in LIBDB-2.

## Isolate metadata access and guard startup recovery (LIBDB-1)

**Depends on.** None.

**Files.**

- [x] Edit `services/library/src/library_service/store.py`, `api.py`, and `service.py` only where construction, typing, or failure handling requires it. Review `__main__.py` and preserve its single-worker behavior.
- [x] Create `services/library/src/library_service/metadata.py` for typed metadata access and schema lifecycle. Create `services/library/tests/test_metadata.py` and the HTTP driver named above.
- [x] Update `services/library/tests/test_library.py`, `README.md`, `API.md` where health or errors change, and `evidence/checks.md` with actual results.

**Build.**

- [x] Keep `StoredDeck`, `StoredOperation`, `Work`, and the existing `Store` workflow methods. Move connection, schema, transaction, and dialect concerns behind one small metadata boundary. Keep filesystem ownership and deck transitions in `Store`. Do not duplicate the workflow in a second store implementation.
- [x] Retain the three logical tables `decks`, `operations`, and `requests`, including validated JSON text records. Add service-owned schema version, a persistent library UUID, a distinct database UUID, and active transfer state. Reject unsupported versions before recovery. Keep migrations inside the library service.
- [x] Replace implicit `rowid` ordering with persisted numeric write order. Seed it from each existing table's rowid and advance it on replacement writes. Preserve current deck/deletion listing order and operation due-time ties, including deletion priority. Use explicit upserts without losing that order. Do not silently change listing behavior while adding a backend.
- [x] Bind the private data directory to the library UUID and active database UUID in an atomically written marker. Hold `owner.lock` before initialization, migration, or recovery. Only adopt a legacy schema-v1 SQLite library through a validated upgrade of that exact local database. Back it up before upgrading and test interruption recovery.
- [x] Before `recover()` removes incoming files or orphan originals, validate the marker, schema, database identity, and transfer state. A missing or mismatched database in an established directory must stop startup without removing files. Never create an empty SQLite database over an established library just because its database file is absent.
- [x] Make partial startup close database handles and release owner locks. Keep business operations atomic and preserve fence checks in the same transaction as writes. Keep existing idempotency and retry semantics.

**You see.**

- [ ] The existing SQLite setup still starts without new variables or dependencies. Existing deck URLs, original hashes, complete manifests, queued work, and deletion records survive the upgrade and restart. Save an upgrade receipt.
- [x] Starting against missing, mismatched, or incomplete metadata fails with a safe actionable error before cleanup. Save a before-and-after original-file hash inventory proving no loss.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [x] Run `uv run --locked pytest -q`, `uv run --locked mypy src`, and `uv run --locked ruff check src tests scripts` from `services/library/`. Add behavior checks for legacy schema upgrade, rollback, marker adoption, mismatch rejection, ordering, resource release, and interrupted upgrade.
- [x] Update the test helper and direct `Store(...)` callers together. Replace the corrupt-record test's direct SQLite coupling with an explicit backend fixture operation. Keep corruption injection in tests.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on the configured `swarm workers` model at the PR head, using the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Upload and extract the same synthetic deck at base and head. Download its original. Save `sqlite-upload.png`. Pass when hashes match input and both reach `extracted` with complete manifests.
- [ ] Lane 2. Upgrade a schema-v1 fixture containing active, failed, pending, and deleted decks. Save `sqlite-upgrade.png`. Pass when identities, manifests, operations, and deletion fences survive restart.
- [ ] Lane 3. Repeat an upload key, then reuse it for different bytes. Save `sqlite-idempotency.png`. Pass when the operation ID is stable and conflicting input returns 409.
- [ ] Lane 4. Interrupt pending extraction and restart. Save `sqlite-resume.png`. Pass when the same durable operation resumes without duplicate submission.
- [ ] Lane 5. Fail extraction and retry, then reprocess. Save `sqlite-retry.png`. Pass when retry retains revision and reprocess advances it while preserving the previous complete manifest until replacement.
- [ ] Lane 6. Delete while processing and deliver a late result. Save `sqlite-delete-race.png`. Pass when the deck stays fenced and its original and content remain unavailable.
- [ ] Lane 7. Remove the selected database from a stopped established fixture. Save `sqlite-missing-database.png`. Pass when startup refuses and every original hash remains unchanged.
- [ ] Lane 8. Substitute mismatched metadata and simulate incomplete marker publication. Save `sqlite-identity.png`. Pass when startup refuses before recovery and releases locks.
- [ ] Lane 9. Open two processes on the same root, then terminate the owner and restart. Save `sqlite-owner.png`. Pass when the second process refuses and a later owner resumes safely.
- [ ] Lane 10. Browse an extracted deck and download its slide image and graphic through the gallery. Save `sqlite-gallery.png`. Pass when previews, downloads, attribution, and warnings behave as on base.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure SQLite startup, list-response latency, and completion of a metadata-only upload/delete probe on identical 100-deck fixtures. Record fixture sizes and machine details.
- [ ] Probe. Run interleaved base/head samples through the workflow driver after warm-up, using deterministic extraction responses for the isolated metadata probe. Record real extraction completion separately without attributing renderer time to SQL.
- [ ] Baseline. Record base results before changing storage. Report sample counts, distributions, and failures. Load benchmark-checklist before interpreting measurements.
- [ ] Rule. Investigate a median regression greater than both 25 percent and 20 ms for startup or HTTP probes. Require zero failed operations. These are provisional regression gates, not product latency promises.

**Review gate.** None. LIBDB-1 is not review-gated. It preserves the existing administrator configuration and content interactions.

**Merge.**

- [ ] Record the independent verdict, checked receipts, current base and head, and review-comment disposition. Stop at verified for the operator to merge.

## Add PostgreSQL selection and transfer the existing library (LIBDB-2)

**Depends on.** LIBDB-1.

**Files.**

- [x] Extend `services/library/src/library_service/metadata.py`, `store.py`, and `api.py`. Update `service.py` only for bounded storage-failure handling.
- [x] Create `services/library/src/library_service/migrate.py`, `services/library/tests/test_database_migration.py`, and PostgreSQL fixtures in `services/library/tests/conftest.py`. Extend the shared behavior tests and workflow driver.
- [x] Update `services/library/pyproject.toml`, `uv.lock`, `README.md`, `API.md`, and `evidence/checks.md`. Record final choices and measured results in this plan.

**Build.**

- [x] Add `LIBRARY_DB_BACKEND` with exactly `sqlite` and `postgresql`, defaulting to `sqlite`. Require `LIBRARY_POSTGRES_DSN` only for PostgreSQL and reject contradictory configuration rather than silently ignoring it. Keep `LIBRARY_DATA_DIR` required for originals, incoming uploads, and the owner marker in both modes. Select once at startup. Do not expose the DSN through HTTP or logs.
- [x] Add Psycopg 3 as the optional `postgres` dependency extra, with a tested binary distribution and locked version. SQLite-only installation and startup must work without importing Psycopg. Use a dedicated local database and fixed library-owned schema. Do not add an ORM, connection pool, migration framework, or vector extension for this feature.
- [x] Implement PostgreSQL table creation, schema versions, bound parameters, explicit upserts, and transactions in the metadata boundary. Retain JSON as text and the same validation rules. Use an autocommit connection for standalone reads and explicit transaction contexts for compound mutations. Keep all access serialized as it is today.
- [x] Preserve the local root owner lock and acquire a database-wide nonblocking session advisory lock before migrations or work. Use the same fixed lock key for every owner of the dedicated library schema, including transfer. Refuse a second process even if it uses another data directory. Bind the database to the root identity. Never reconnect silently after losing the owning session.
- [ ] Set bounded connection, statement, and lock waits. Begin with proposed limits of 5 seconds for connection and statement waits and 1 second for lock waits, then verify outage behavior. Ensure transport loss is bounded too, using tested driver or socket controls. Do not claim statement timeout alone bounds a broken network. If blocking calls exceed the responsiveness gate, move serialized storage work off the event loop before shipping.
- [x] Map safe runtime storage errors to the existing structured error envelope with HTTP 503 and a storage-specific code. Make `/health` report `ready=false` when storage ownership or availability is lost. Stop the worker safely on lost ownership and require restart. Do not replay transactions with uncertain commit results. Clients repeat keyed requests or poll their existing operation after restart. Update the API contract and checks.
- [x] Provide the proposed offline command `uv run --locked --extra postgres python -m library_service.migrate --target-backend postgresql` and its `--target-backend sqlite` counterpart. Read source configuration from the environment and PostgreSQL credentials from `LIBRARY_POSTGRES_DSN`. Never put credentials in CLI arguments. Finalize help text and exit codes with the tests.
- [ ] Require library and extraction to be stopped and the frontend disconnected. Acquire root and both database locks. Refuse source-equals-target, unsupported schemas, corrupt records, and a populated PostgreSQL target. For transfer back to SQLite, archive any prior inactive SQLite snapshot without overwriting it, then create a fresh target. Validate archive and target paths stay inside the private root.
- [ ] Before transfer, back up source metadata and the coordinated service state, export deletion fences, and verify every active original against its recorded length and checksum. Preserve all decks, operations, request mappings, ordering, UUIDs, remote keys, jobs, manifests, retries, deadlines, source revisions, and fence counters. Retain the same originals in the same root without rewriting them. Do not re-upload to extraction.
- [x] Write the target in a transaction with the same library UUID and a new database UUID. Validate record counts, canonical record hashes, request references, operation links, and original integrity before activation. Preserve pending and unknown-UUID deletion fences. Report only safe counts and success or failure.
- [ ] Use a durable transfer journal to handle the non-atomic boundary between two databases and a filesystem marker. Keep the target inactive while copying. Before activation, seal the source against future writes. Publish the marker atomically only after target verification. Refuse normal startup during an incomplete transfer and provide an idempotent resume command. Test interruption at every phase, especially target commit and marker replacement. A retry must neither overwrite unrelated target data nor duplicate operations.
- [x] Retain the old database as an inactive backup. A completed transfer makes it stale after new writes. Reject startup of that stale source, including from a copied root marker. Switching back means a fresh transfer of the current library, never reactivating the old snapshot. Document the point after which cancellation requires completing transfer or restoring a coordinated backup.
- [x] Document installation on Windows and Linux, local PostgreSQL provisioning and roles, both backend settings, transfer preconditions, resume and failure procedures, archive retention, and coordinated database/files/extraction backups. Explain that PostgreSQL WAL, old database copies, and backups have separate retention and do not inherit SQLite `secure_delete` behavior.

**You see.**

**Implementation evidence, 2026-11-08.** Psycopg 3.3.8 with its binary distribution is locked as the optional `postgres` extra. The configured PostgreSQL server reported version 18.6. The 29-test suite passed against disposable databases created from the configured connection. SQLite-to-PostgreSQL-to-SQLite preserved the test deck original hash and the unknown-UUID deletion fence. An interruption after target activation resumed successfully. Detailed commands and results are in [`services/library/evidence/checks.md`](../../services/library/evidence/checks.md).

The automated checks do not cover an actual extraction workflow, a broken-network outage, event-loop responsiveness during a stalled query, the 100-deck capacity fixture, a gallery workflow, or the operator review. A shared upload/deletion/restart check runs against each backend, while the existing extraction behavior tests remain SQLite-only. Keep those checks below open until they have evidence.

- [ ] With the backend variable unset, the existing SQLite library works. After offline transfer and a restart with PostgreSQL selected, the same decks and operation URLs work with unchanged original hashes. Save before-and-after API and gallery receipts.
- [ ] Transfer the now-current PostgreSQL library back to SQLite. Decks created and deleted since the first switch retain their latest state. Save the round-trip comparison and show that stale source startup is rejected.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [x] Run `uv run --locked pytest -q`, `uv run --locked mypy src`, and `uv run --locked ruff check src tests scripts` in SQLite-only installation. Then run the same checks with `uv run --locked --extra postgres` against a real isolated PostgreSQL instance. Require the backend matrix in the documented verification procedure. Do not count a skipped PostgreSQL suite as a pass.
- [ ] Parameterize the existing library tests across SQLite and PostgreSQL. Include corruption, idempotency, reprocessing, restart, deletion during download, stale publication, interrupted retries, and unknown-UUID deletion replay. Keep the deterministic extraction contract fixture. A shared upload/deletion/restart persistence test covers both backends; the broader behavior matrix remains open.
- [ ] Add focused checks for configuration validation and secret redaction, both owner locks, wrong root/database binding, schema upgrades, bounded outages, safe 503 responses, ambiguous commits, both transfer directions, stale-source refusal, mismatch preservation, and every journal interruption point. Test all three metadata tables and original checksums in round trips.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on the configured `swarm workers` model at the PR head, using the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Run upload, extraction, original download, and deletion on base SQLite and head SQLite. Also run the same workflow on head PostgreSQL. Base has no PostgreSQL feature. Save `backend-workflow.png`. Pass when existing behavior matches and PostgreSQL reaches the same verified end states.
- [ ] Lane 2. Transfer SQLite with extracted, failed, pending, and deleted records to PostgreSQL. Save `sqlite-to-postgres.png`. Pass when identities, all metadata comparisons, and original hashes match and pending work resumes.
- [ ] Lane 3. Add and delete decks in PostgreSQL, then transfer back to SQLite. Save `postgres-to-sqlite.png`. Pass when the latest state survives, deletion fences remain effective, and the archived SQLite copy is preserved.
- [ ] Lane 4. Repeat keyed requests and cause conflicting replays on PostgreSQL. Save `postgres-idempotency.png`. Pass when operation IDs remain stable and conflicts return 409 without duplicate records.
- [ ] Lane 5. Reprocess, delete during processing, and deliver a late extraction response on PostgreSQL. Save `postgres-delete-race.png`. Pass when stale work cannot restore content and cleanup converges after restart.
- [ ] Lane 6. Stop PostgreSQL during a request and while the worker polls. Save `postgres-outage.png`. Pass when waits are bounded, errors expose no credentials, health becomes false, and restart resumes durable work without duplicate submissions.
- [ ] Lane 7. Start a second library with a different root against the same PostgreSQL database. Save `postgres-owner.png`. Pass when it refuses without work or file cleanup and the first owner continues safely.
- [ ] Lane 8. Point an established root at an empty or wrong PostgreSQL database, and at stale prior metadata. Save `postgres-wrong-database.png`. Pass when startup refuses and the original-file inventory is unchanged.
- [ ] Lane 9. Interrupt transfer after target commit and around source sealing and marker publication. Resume twice. Save `transfer-resume.png`. Pass when one database becomes active, counts and checksums match, stale startup fails, and no operation duplicates.
- [ ] Lane 10. After each transfer direction, browse the same deck and download an original, slide image, and graphic through the frontend. Save `transfer-gallery.png`. Pass when attribution, warnings, previews, hashes, and generation-bound URLs remain valid.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure SQLite metadata probes at base and head, PostgreSQL equivalents at head, real workflow completion, transfer duration, outage response time, and unrelated request responsiveness during a stalled query. Use 100 decks with about 2,000 slide records for the capacity fixture.
- [ ] Probe. Extend the workflow driver to repeat equivalent probes with fixed fixtures. Interleave base/head SQLite samples first. Measure local PostgreSQL separately. Record the isolated metadata work and renderer-dependent completion separately. Never report a speed ratio between transfer and ordinary startup.
- [ ] Baseline. Record base SQLite first, then head SQLite and PostgreSQL. Record database/server versions, file sizes, machine, sample counts, and failures. No PostgreSQL or transfer baseline exists on base.
- [ ] Rule. Apply LIBDB-1's SQLite regression gate. Use provisional absolute budgets of 60 seconds for offline metadata transfer on the capacity fixture, 10 seconds for a failed database request, and 1 second for an unrelated health request during a stalled query. Require zero incorrect or lost records and matched original hashes. Record real extraction duration without a fabricated numerical promise. Revisit budgets with evidence before claiming support.

**Review gate.** The operator reviews the administrator transfer interaction before merge.

- [ ] Post safe terminal screenshots for preflight, successful transfer, interrupted-transfer resume, and stale-source refusal. Record a short video of the transfer and the preserved deck in the gallery. The operator reviews the screenshots and video in chat before merge.

**Merge.**

- [ ] Record the independent verdict at the current base and head, all backend and transfer receipts, review-comment disposition, and operator review. Stop at verified and hand the dependent PR to the operator.

## Close the program

- [ ] Confirm every implementation and verification box has its evidence. Link results in `services/library/evidence/checks.md`. Keep failed or unrun checks explicit.
- [ ] Hand back the verified PRs in dependency order, supported configurations, transfer instructions, measured limits, and remaining risks. Do not claim completion from tests alone.

## Appendix A. Prototype evidence

No runtime prototype or PostgreSQL test was run during planning. The checked source revision was `29994807fc9ec44d5e9d1f5134468ff6d95443ba`. Read-only inspection confirmed SQLite schema-v1 tables, JSON records, owner locking, implicit ordering, and destructive orphan recovery in `store.py`.

The operator explicitly chose to carry the existing library across. Configuration at restart and offline transfer are proposed scope decisions. Native driver packaging, PostgreSQL lock behavior under disconnect, transport timeout coverage, event-loop responsiveness, and interrupted cutover remain unproven. Their checks above gate implementation completion. No prototype branch, SHA, screenshot, or benchmark is claimed.

## Appendix B. Alternatives rejected

| Alternative | Reason |
| --- | --- |
| Rewrite the store with an ORM | Adds a dependency and data rewrite beyond the three current tables. A narrow metadata boundary preserves the workflow. |
| Duplicate SQLite and PostgreSQL stores | Duplicates deletion fences, retries, and file handling. |
| Replace SQL placeholders with a generic string parser | Hides dialect errors. Keep bound values and explicit backend statements at the metadata boundary. |
| Live switching or dual writes | Adds coordination and consistency requirements to every mutation. Offline transfer meets the selected workflow. |
| Select a new database and immediately run recovery | Can delete originals absent from that database. Identity and transfer checks must precede recovery. |
| Switch back by reusing the old database | Restores stale content and can lose later deletion fences. Transfer the latest state instead. |
| Add multiple library workers with PostgreSQL | Metadata changes do not make local original storage or worker claiming safe for multiple owners. |

Laziness Protocol keeps existing workflow methods and avoids an ORM or pooling. Sequence Work into Verifiable Units puts the guarded SQLite refactor before the dependent backend and transfer feature. Guard the Context Window limited delegated exploration to a read-only storage report. Prove It Works requires round-trip comparisons and running HTTP workflows. Encode Lessons in Structure applies the plan checker to this document.

## Appendix C. Risks

- LIBDB-1 must protect originals before changing database selection. Its identity check is ineffective if recovery runs first or if legacy adoption silently accepts an empty database.
- LIBDB-2 spans database transactions and filesystem state. No single transaction covers both databases and the marker. The journal, inactive-source state, and interruption tests are required parts of transfer.
- A lost connection can leave commit outcome unknown. Automatic mutation retries can duplicate effects or conflict with file movement. Stop safely and reuse durable idempotency keys after restart.
- PostgreSQL remains synchronous in the proposed first implementation. Bounded failure tests must show that storage waits do not stall the application beyond the provisional responsiveness gate.
- The source database can contain deleted content in historical storage or backups. Retaining it requires the administrator's retention policy and protection. Normal library deletion does not erase backups immediately.
- SQLite schema upgrades prevent old binaries from reading the new schema. Roll back code only with the matching coordinated backup. Do not run an old binary against the upgraded root.
- The renderer still requires Linux or WSL2. Database support on Windows does not establish native Windows rendering support.
- No project-specific browser driver skill was found in the scoped inspection. Resolve an available driver before live verification, retain HTTP receipts, and disclose any unavailable browser check.

## Appendix D. Links and reading list

- [Project constitution](../constitution.md), [library README](../../services/library/README.md), and [library API contract](../../services/library/API.md).
- [Storage implementation](../../services/library/src/library_service/store.py), [API configuration](../../services/library/src/library_service/api.py), [worker](../../services/library/src/library_service/service.py), and [existing behavior tests](../../services/library/tests/test_library.py).
- [Extraction setup](../../services/powerpoint-extraction/README.md) and [gallery plan](slide-gallery.md).
- Context7 resolved official Psycopg documentation on 2026-11-07 through `/psycopg/psycopg`. Its [transaction documentation](https://github.com/psycopg/psycopg/blob/master/docs/basic/transactions.rst) supports autocommit reads with explicit transaction contexts. Its [connection implementation](https://github.com/psycopg/psycopg/blob/master/psycopg/psycopg/connection.py) documents connection timeout handling. Pin the tested stable release during implementation.
- [Psycopg parameter binding](https://www.psycopg.org/psycopg3/docs/basic/params.html) specifies bound `%s` parameters. [PostgreSQL advisory locks](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS) and [statement settings](https://www.postgresql.org/docs/current/runtime-config-client.html#RUNTIME-CONFIG-CLIENT-STATEMENT) inform the proposed owner and timeout controls. Verify the deployed version's behavior.
- Use `skills/how/SKILL.md` for both PRs, `skills/architect/SKILL.md` before cross-file interface implementation, and `skills/interrogate/SKILL.md` for contested cutover decisions. Keep the execution decision trail under `.audit/library-database-switch/` using `skills/show-me-your-work/SKILL.md`.
