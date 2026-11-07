# PowerPoint library service

This independently startable Python service owns canonical uploaded decks and durable extraction operations. It sends exact original bytes to the extraction service over authenticated HTTP. It publishes complete manifest snapshots and proxies graphic downloads through the library authorization boundary. Completed processing is labelled `extracted`. Search indexing is a later feature.

## Install and start

Use Python 3.12 or later and `uv`. From `services/library/`, install the locked dependencies:

```powershell
uv sync --locked
$env:LIBRARY_TOKEN = '<random printable secret of at least 32 bytes>'
$env:EX1_TOKEN = '<the extraction service token>'
$env:EXTRACTION_URL = 'http://127.0.0.1:8001'
$env:LIBRARY_DATA_DIR = "$env:LOCALAPPDATA\PowerPointSearcher\library"
$env:LIBRARY_ORIGINS = 'http://localhost:5173'
uv run python -m library_service
```

The service listens on `127.0.0.1:8000`. `LIBRARY_HOST` and `LIBRARY_PORT` change that listener. The library can start when extraction is unavailable. Accepted work remains durable and temporary network failures retry with bounded backoff. After five consecutive processing failures, status becomes `failed` with an actionable retry. A full extraction queue keeps accepted work queued with capped backoff. Deletion continues retrying until both local and extraction cleanup complete.

On Linux, use a private directory under the service user's home, such as `LIBRARY_DATA_DIR="$HOME/.local/share/powerpoint-library"`. On Windows, use a dedicated NTFS directory. The service rejects links and non-NTFS Windows data volumes, protects the directory for the current user, and holds an exclusive lifetime owner lock. Run one service instance and one server worker per data directory.

Start extraction separately using its [README](../powerpoint-extraction/README.md). Its current renderer requires Linux or WSL2. Native Windows rendering fails closed. The extraction service's source-inspection confinement and rendering limitations also apply to this workflow. No cloud service is required.

## Public interface

The frontend calls only library HTTP routes. All content routes require `Authorization: Bearer <LIBRARY_TOKEN>`. The extraction token remains server-side. `LIBRARY_ORIGINS` is a comma-separated list of exact frontend origins for browser access. CORS is disabled when that variable is empty.

The [API contract](API.md) defines upload, operation status, retry, reprocessing, generation manifests, original and graphic downloads, deletion, and a minimal deletion-fence export. Configuration rejects extraction URLs with embedded credentials. Outbound HTTP has explicit timeouts, ignores proxy environment variables, and does not follow redirects.

Each original is immutable and named by deck UUID in private storage. A retry preserves the source revision. Reprocessing increments it, preserves the same canonical bytes, and keeps the previous complete manifest available until the replacement is validated. The library never imports extraction code or reads its database or files.

Manifest JSON contains text, geometry, warnings, and asset metadata. It contains no image bytes or private filesystem paths. Asset links refer to authenticated library routes tied to one generation. Original embedded formats are attachments. Only PNG renditions are suitable for inline previews. Contextual crops retain their warning metadata and may contain surrounding objects or slide background.

## Verify independently

```powershell
uv run --locked pytest -q
uv run --locked mypy src
uv run --locked ruff check src tests
```

These checks exercise the actual HTTP adapter against a deterministic extraction contract, interruption recovery, lost retry responses, supersession, deletion during downloads, replay of unknown-UUID deletion fences, authentication before body reads, manifest validation, and original checksums. They do not establish renderer fidelity, platform acceptance, or the initial 100-deck performance target. The complete live workflow also needs the extraction service and `frontend/`.

Default guardrails are 64 MiB per original and asset, 65 MiB per complete incoming request, 8 MiB per extraction JSON response, a 30-second incoming-body deadline, a 15-second outbound request timeout, and one-second job polling. They are guardrails, not measured capacity or latency promises. `Settings` supplies these values for deployments that need different limits.

## Back up and restore

Stop the library and extraction services before taking a consistent local snapshot. Back up the complete library data directory, including `metadata.sqlite3`, `originals/`, and the operation and deletion records in metadata. Back up extraction's complete service-owned data directory separately. Export `GET /v1/deletions` to a separate protected backup location before stopping the services. Do not copy only originals or only manifests. The two services' pending operations and deletion fences are required to resume safely.

Retain the newest deletion identities separately from older content snapshots. Compare their `updated_at` values with the snapshot time. A backup policy must preserve deletion records created after a content backup. Backup copies can retain deleted content until the administrator's documented retention period expires. Active-library deletion does not promise immediate deletion from backups.

To restore, stop both services and preserve their current data directories before replacing them. Restore both snapshots. Start the services on loopback without reconnecting the frontend. Replay every deletion UUID recorded after the snapshot through the authenticated library `DELETE /v1/decks/{deck_id}` route. Wait for each deletion status to become `deleted`. Repeated deletion is safe. The library resumes pending extraction and cleanup operations at startup. Reconnect the frontend only after the newest deletion fences and pending cleanup have converged. If the newest deletion export is unavailable, an older backup cannot safely be exposed as the active library.

The library's canonical copy remains until deletion is fenced locally. The cleanup worker removes it before waiting for extraction, even during an extraction outage. Extraction retains a private work copy for retries and all derived assets until its deletion completes. There is no third copy of derivatives in the library. Retired extraction generations can make an older open preview unavailable, so clients must refresh the whole manifest after a generation change.
