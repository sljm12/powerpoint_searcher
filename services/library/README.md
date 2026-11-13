# PowerPoint library service

This independently startable Python service owns canonical uploaded decks and durable extraction operations. It sends exact original bytes to the extraction service over authenticated HTTP. It publishes complete manifest snapshots and proxies graphic downloads through the library authorization boundary. Completed processing is labelled `extracted`. Search indexing is a later feature.

## Install and start

Use Python 3.12 or later and `uv`. From `services/library/`, install the locked dependencies and start the service.

### Linux

```sh
uv sync --locked
export LIBRARY_TOKEN='<random printable secret of at least 32 bytes>'
export EX1_TOKEN='<the extraction service token>'
export EXTRACTION_URL='http://127.0.0.1:8001'
export LIBRARY_DATA_DIR="$HOME/.local/share/powerpoint-library"
export LIBRARY_ORIGINS='http://localhost:5173'
uv run python -m library_service
```

### Windows PowerShell

```powershell
uv sync --locked
$env:LIBRARY_TOKEN = '<random printable secret of at least 32 bytes>'
$env:EX1_TOKEN = '<the extraction service token>'
$env:EXTRACTION_URL = 'http://127.0.0.1:8001'
$env:LIBRARY_DATA_DIR = "$env:LOCALAPPDATA\PowerPointSearcher\library"
$env:LIBRARY_ORIGINS = 'http://localhost:5173'
# Optional: configure both values to enable text indexing and grouped search.
$env:LIBRARY_SEARCH_URL = 'http://127.0.0.1:8002'
$env:LIBRARY_SEARCH_TOKEN = '<the search service token>'
uv run python -m library_service
```

The service listens on `127.0.0.1:8000`. `LIBRARY_HOST` and `LIBRARY_PORT` change that listener. `LIBRARY_INCLUDE_NOTES` controls speaker-note extraction and defaults to `true`; set it to `false` to exclude notes. The authenticated `GET /v1/policy` route reports the active value. The frontend shows the policy before upload. The library captures it when it accepts an upload or reprocess operation, and retries keep that captured value.

Text search is optional. Configure both `LIBRARY_SEARCH_URL` and `LIBRARY_SEARCH_TOKEN` after independently starting and migrating `services/search/`. The library persists each bounded text snapshot with its extraction publication, submits it with a stable key, and polls status. Search failure does not block uploads or gallery use. A deck is shown as searchable only after the complete build is ready. Deletion sends the search fence and waits for search, extraction, and original cleanup. The library is the only HTTP service the browser calls.

Retryable indexing failures show **Retry search indexing** on the deck. This reuses the search job's durable input snapshot and does not rerun PowerPoint extraction. Configuration and model errors remain failed until corrected or the deck is reprocessed under the active profile.

The library can start when extraction is unavailable. Accepted work remains durable and temporary network failures retry with bounded backoff. After five consecutive processing failures, status becomes `failed` with an actionable retry. A full extraction queue keeps accepted work queued with capped backoff. Deletion continues retrying until both local and extraction cleanup complete.

Stored operations created before notes-policy capture have no recorded policy. The library submits those unfinished operations with notes excluded, which preserves their pre-feature behavior. Retrying one of these operations keeps notes excluded. Use reprocess to apply the current policy.

## Use PostgreSQL

SQLite remains the default. Install Psycopg 3 with its binary distribution only when you select PostgreSQL:

```sh
uv sync --locked --extra postgres
export LIBRARY_DB_BACKEND=postgresql
export LIBRARY_POSTGRES_DSN="$VECTOR_DB"
```

The lock pins Psycopg 3.3.8 and its binary distribution. On Windows PowerShell, run `uv sync --locked --extra postgres`, set `$env:LIBRARY_DB_BACKEND = 'postgresql'`, and set `$env:LIBRARY_POSTGRES_DSN` from your protected environment. The example `set_db.bat` names its connection setting `VECTOR_DB`. Do not put the connection string in a command argument or commit it.

Use a dedicated local PostgreSQL 18 database. The library owns the fixed `powerpoint_library` schema and refuses databases that contain other user tables or a schema bound to another library. Give the service role `CONNECT` and `CREATE` on the database. PostgreSQL owns the schema and its tables after first startup. PostgreSQL uses a 5 second connection timeout, a 5 second statement timeout, a 1 second lock timeout, TCP keepalives, and a 5 second TCP user timeout. The service holds a nonblocking database session lock for its lifetime. It does not reconnect after it loses that session.

Set `LIBRARY_DATA_DIR` in both database modes. The root marker binds the data directory to the library and the selected database. A PostgreSQL marker also binds the database to the canonical data-root path. Keep the marker and originals together. To move a PostgreSQL library, transfer it to SQLite, move the complete root, then transfer it to a fresh PostgreSQL database at the new path.

The service returns HTTP 503 with code `storage_unavailable` when storage fails. `/health` reports `ready=false` when storage is unavailable or the worker has stopped. Restart the service after a storage failure. Repeat mutations with their original idempotency key after restart.

## Transfer the existing library

Before transfer, stop the library and extraction services, disconnect the frontend, and take a coordinated backup of the library and extraction data directories. The command checks the library owner lock. The `--confirm-extraction-stopped` flag records the operator's confirmation for the extraction service and frontend.

From `services/library/`, transfer SQLite metadata to PostgreSQL with:

```powershell
uv run --locked --extra postgres python -m library_service.migrate --target-backend postgresql --confirm-extraction-stopped
```

The command reads `LIBRARY_DATA_DIR` and `LIBRARY_POSTGRES_DSN` from the environment. It prints only the row counts and completion status. Restart the library with `LIBRARY_DB_BACKEND=postgresql` after the command succeeds.

To transfer the current PostgreSQL library back to SQLite, keep `LIBRARY_POSTGRES_DSN` set and run:

```powershell
uv run --locked --extra postgres python -m library_service.migrate --target-backend sqlite --confirm-extraction-stopped
```

Restart the library with `LIBRARY_DB_BACKEND=sqlite`. The command archives an earlier inactive SQLite file as `metadata.sqlite3.inactive.<database-id>` and creates a fresh SQLite database. It never reactivates that older snapshot.

The command writes `library-transfer.json` as a durable journal and `metadata-transfer-<id>.json` as a private metadata backup. Keep both until you verify the transfer. If the command stops before it reports success, keep both services stopped and rerun the same command with the same target. Normal library startup refuses an incomplete transfer. Do not edit the marker or delete the journal to force startup. Once the journal records `source_sealed`, finish the transfer or restore a coordinated backup. The source database remains sealed as an inactive backup after a successful transfer.

Use a fresh, empty PostgreSQL database as a target. A populated PostgreSQL target fails preflight and leaves the active source untouched. The command verifies all deck, operation, and request rows, references, record hashes, and original file hashes. It keeps the same originals and does not submit extraction work again. The extraction service's data is not copied or changed by the transfer.

On Linux, use a private directory under the service user's home. On Windows, use a dedicated NTFS directory. The service rejects links and non-NTFS Windows data volumes, protects the directory for the current user, and holds an exclusive lifetime owner lock. Run one service instance and one server worker per data directory.

Start extraction separately using its [README](../powerpoint-extraction/README.md). Its current renderer requires Linux or WSL2. Native Windows rendering fails closed. The extraction service's source-inspection confinement and rendering limitations also apply to this workflow. No cloud service is required.

## Public interface

The frontend calls only library HTTP routes. All content routes require `Authorization: Bearer <LIBRARY_TOKEN>`. The extraction token remains server-side. `LIBRARY_ORIGINS` is a comma-separated list of exact frontend origins for browser access. CORS is disabled when that variable is empty.

The [API contract](API.md) defines upload, operation status, retry, reprocessing, generation manifests, original and graphic downloads, deletion, and a minimal deletion-fence export. Configuration rejects extraction URLs with embedded credentials. Outbound HTTP has explicit timeouts, ignores proxy environment variables, and does not follow redirects.

Each original is immutable and named by deck UUID in private storage. A retry preserves the source revision. Reprocessing increments it, preserves the same canonical bytes, and keeps the previous complete manifest available until the replacement is validated. The library never imports extraction code or reads its database or files.

The first startup creates `library.json`, which binds this data directory to its library and SQLite database UUIDs. Keep the marker, `metadata.sqlite3`, and `originals/` together in backups. Startup refuses a missing database, a mismatched marker, or incomplete initialization before it removes incoming files or orphan originals. An unmarked schema-v1 database is upgraded only after its records and original-file hashes validate. The service keeps a `metadata.sqlite3.v1-backup` copy before migration.

If an upgrade stops after the marker enters `preparing`, keep the service stopped. Preserve the whole data directory, restore `metadata.sqlite3` from `metadata.sqlite3.v1-backup`, remove the incomplete `library.json`, then restart the service to retry the upgrade. Do not delete the marker or backup before checking that the backup is the matching schema-v1 database. A schema-v1 library with no originals has no stored UUID, so local files cannot prove its prior identity. Use a coordinated backup when provenance matters.

If a new, empty library stops during its first initialization, preserve that directory for diagnosis and configure `LIBRARY_DATA_DIR` to a different empty directory. The interrupted directory has no coordinated backup and cannot start while its marker says `preparing`.

Manifest JSON contains visible slide text, speaker notes, geometry, warnings, and asset metadata. Notes are a separate slide channel. All authorized users of the shared library can read them through the manifest. The library does not log note text. Disabling extraction does not remove notes from the preserved original. The manifest contains no image bytes or private filesystem paths. Asset links refer to authenticated library routes tied to one generation. Original embedded formats are attachments. Only PNG renditions are suitable for inline previews. Contextual crops retain their warning metadata and may contain surrounding objects or slide background.

## Verify independently

```powershell
uv run --locked pytest -q
uv run --locked mypy src
uv run --locked ruff check src tests
uv run --locked python scripts/check_database_workflow.py --self-check
```

These checks exercise the actual HTTP adapter against a deterministic extraction contract, interruption recovery, lost retry responses, supersession, deletion during downloads, replay of unknown-UUID deletion fences, authentication before body reads, manifest validation, and original checksums. They do not establish renderer fidelity, platform acceptance, or the initial 100-deck performance target. The complete live workflow also needs the extraction service and `frontend/`.

Default guardrails are 64 MiB per original and asset, 65 MiB per complete incoming request, 8 MiB per extraction JSON response, a 30-second incoming-body deadline, a 15-second outbound request timeout, and one-second job polling. They are guardrails, not measured capacity or latency promises. `Settings` supplies these values for deployments that need different limits.

## Back up and restore

Stop the library and extraction services before taking a consistent local snapshot. Back up the complete library data directory, including `metadata.sqlite3`, `originals/`, and the operation and deletion records in metadata. Back up extraction's complete service-owned data directory separately. Export `GET /v1/deletions` to a separate protected backup location before stopping the services. Do not copy only originals or only manifests. The two services' pending operations and deletion fences are required to resume safely.

Retain the newest deletion identities separately from older content snapshots. Compare their `updated_at` values with the snapshot time. A backup policy must preserve deletion records created after a content backup. Backup copies can retain deleted content until the administrator's documented retention period expires. Active-library deletion does not promise immediate deletion from backups.

To restore, stop both services and preserve their current data directories before replacing them. Restore both snapshots. Start the services on loopback without reconnecting the frontend. Replay every deletion UUID recorded after the snapshot through the authenticated library `DELETE /v1/decks/{deck_id}` route. Wait for each deletion status to become `deleted`. Repeated deletion is safe. The library resumes pending extraction and cleanup operations at startup. Reconnect the frontend only after the newest deletion fences and pending cleanup have converged. If the newest deletion export is unavailable, an older backup cannot safely be exposed as the active library.

The library's canonical copy remains until deletion is fenced locally. The cleanup worker removes it before waiting for extraction, even during an extraction outage. Extraction retains a private work copy for retries and all derived assets until its deletion completes. There is no third copy of derivatives in the library. Retired extraction generations can make an older open preview unavailable, so clients must refresh the whole manifest after a generation change.

PostgreSQL WAL, inactive database copies, and database backups have separate retention. They do not inherit SQLite's `secure_delete` behavior. Coordinate their retention with the original-file and extraction backups.
