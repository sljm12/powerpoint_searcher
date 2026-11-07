# Extraction HTTP API v1

The service uses REST over HTTP. Content routes require `Authorization: Bearer <EX1_TOKEN>`. Authentication precedes body reads and multipart parsing. The token contains at least 32 bytes. `/health` is the only public route. Responses use `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and a generated UUID `X-Request-ID`.

The authoritative generated response schemas are the Pydantic records in `models.py` and `create_app(...).openapi()`. Public OpenAPI and documentation routes are disabled. Unknown and malformed fields do not echo supplied values.

| Method and path | Request | Success |
| --- | --- | --- |
| `GET /health` | None | 200 `Health` with `ready` only |
| `POST /v1/decks/{deck_id}/extractions` | Multipart `file`, `source_revision`, `idempotency_key` exactly once each | 202 `Job` |
| `GET /v1/jobs/{job_id}` | None | 200 `Job` |
| `POST /v1/jobs/{job_id}/retry` | Empty body; optional `Idempotency-Key` header | 202 `Job` |
| `GET /v1/decks/{deck_id}/manifest` | Optional UUID query `generation` | 200 `Manifest` |
| `GET /v1/assets/{asset_id}` | None | 200 bytes with safe attachment name, stored media type, byte length, and SHA-256 ETag |
| `DELETE /v1/decks/{deck_id}` | None | 202 `Deletion` |
| `GET /v1/decks/{deck_id}/deletion` | None | 200 `Deletion` |

All IDs are UUIDs. Source revision is a positive decimal integer of at most ten digits. Idempotency keys contain 1 to 128 characters from ASCII letters, digits, underscore, period, colon, and hyphen. The filename must end in `.pptx`. The service strips directory components for both slash styles and rejects control characters and basenames longer than 255 characters. It retains this safe display name with the active deck and generation, then removes it on deletion. Private filesystem paths do not enter responses or errors. The source validator checks the actual presentation rather than trusting this suffix. Unsupported legacy `.ppt` files return 415.

## Job and revision behavior

`Job` contains `job_id`, `deck_id`, `source_revision`, `generation_id`, `state`, `stage`, `attempt`, `completed_slides`, `total_slides`, UTC `created_at` and `updated_at`, optional structured `error`, `warnings`, `status_url`, and optional `manifest_url`.

States are `queued`, `processing`, `succeeded`, `failed`, and `cancelled`. Stages are `queued`, `extracting`, `rendering`, and `publishing`. Counts start at zero and the adapter supplies the known total after source inspection. Rendering counts describe the batch before and after conversion. Publication marks all slides complete. A succeeded extraction means complete extraction only, not embedding or search indexing.

Poll once per second initially and back off for long jobs. Honor queue saturation by retrying later. A network timeout during submission is safe to retry with the same idempotency key and identical bytes and revision. The digest includes the API processing version, revision, and exact source checksum. The same key with changed input returns 409. Duplicate input for the same current revision returns its existing job. Changing bytes without increasing revision returns 409. An older revision returns 409 unless it is the exact replay of a persisted idempotency request. A newer revision cancels older unfinished jobs and preserves the old complete generation until replacement succeeds.

Retry accepts only the current failed job with `error.retryable=true`. It creates a new job and generation ID, increases `attempt`, and reuses the revision's work copy. When supplied, `Idempotency-Key` uses the same 1 to 128 character syntax as extraction submission and binds the new job to the parent job ID. Repeating the request with that key returns the same new job, including after another attempt has become current. Reusing the key for a different request returns 409. Without the header, repeating retry on the old job returns 409. A retry against a deleted job returns 404 because deletion removes job records. An old submission idempotency request continues to identify the original attempt.

## Manifests and downloads

`Manifest` contains `schema_version=1`, `deck_id`, `source_revision`, `generation_id`, `source_sha256`, `source_filename`, nullable `canonical_original_reference`, runtime-validated source and rendition `content`, `assets`, and structured `warnings`. Publication and manifest reads validate the content schema and verify that every referenced asset exists in that generation. A malformed or inconsistent stored generation returns 503 and is not reported as complete.

Each asset contains opaque `asset_id`, adapter-local `local_id`, `media_type`, `role`, positive `byte_length`, SHA-256 `sha256`, and relative authenticated `download_url`. Roles distinguish `slide_preview`, `original_picture`, `isolated_render`, `group_render`, and `contextual_crop`. Original embedded bytes remain separate from rendered renditions. Raster metadata records actual pixel dimensions and transparency. The generation selector is constrained to the same deck. Guessed and unpublished asset IDs return 404.

The previous generation remains available while replacement is in progress. Once replacement publishes, the previous generation and assets retire atomically and their selectors return 404. Downloads validate bounded bytes under the metadata lock. Already authorized response bytes can finish transmission after deletion starts.

## Deletion

`Deletion` contains `deck_id`, `state` of `pending` or `complete`, and optional structured `error`. Deleting an unknown UUID creates its tombstone. Repeating deletion returns its existing cleanup state. Future submissions against the UUID return 410.

The first deletion transaction revokes manifests and downloads and removes content metadata. Cleanup remains pending while renderer cancellation or file removal is incomplete. Completion means all deck bytes are absent and the renderer cannot recreate them. Read cleanup until complete; repeat DELETE after a retryable cleanup error. Startup resumes pending cleanup. Backups have separate administrator retention as described in the README.

## Errors and limits

Errors have this envelope. `stage` is null for request errors and identifies the failed stage for job errors.

```json
{"error":{"code":"queue_full","message":"Processing queue is full; retry later.","retryable":true,"stage":null},"request_id":"00000000-0000-0000-0000-000000000000"}
```

| HTTP status | Codes | Meaning |
| --- | --- | --- |
| 400 | `malformed_request`, `upload_timeout` | Invalid UUID, revision, key, fields, multipart, content length, or timed-out body |
| 401 | `unauthorized` | Missing or incorrect service bearer token |
| 404 | `not_found` | Unknown, deleted job, unpublished asset, retired generation, or foreign-deck generation |
| 409 | `idempotency_conflict`, `revision_conflict`, `stale_revision`, `superseded`, `not_retryable` | Conflicting input or invalid lifecycle transition |
| 410 | `deleted` | Deck UUID has a durable tombstone |
| 413 | `request_limit`, `upload_limit` | Bounded body or uploaded file limit exceeded |
| 415 | `unsupported_type` | Wrong request format or filename suffix |
| 422 | `invalid_presentation` | Empty, malformed, unsafe, or over-limit presentation |
| 429 | `queue_full` | Queued and processing jobs fill the configured queue |
| 503 | `renderer_unavailable`, `service_unavailable`, `manifest_unavailable`, `asset_unavailable` | Missing confined renderer, unavailable local dependency, or invalid stored output |

Accepted processing failures appear in the 200 job response. Codes are `conversion_failed`, `conversion_timeout`, `worker_stopped`, and `worker_interrupted`. They are retryable. Private exception messages never enter responses. Cleanup can report retryable `cleanup_failed` while remaining pending.

Default limits are documented in the README. The complete request limit runs before multipart parsing even when Content-Length is missing. Multipart permits one file and two fields, bounds field parts to 512 bytes, and rejects repeated or extra fields. The worker runs one extraction at a time. Authentication and size rejection happen before presentation inspection.

## Runtime and platform limits

LibreOffice conversion uses a private profile, macros disabled, link updates disabled, bounded execution, and an OS confinement launcher. Linux uses bubblewrap namespaces plus delegated cgroup v2 limits. Python OOXML validation and source inventory still run in the service process and are not sandboxed. Native Windows is not ready: the tested AppContainer cannot connect to LibreOffice's UNO pipe. Readiness remains false and uploads fail with 503. Do not expose the service on an untrusted network or weaken the fail-closed boundary.

Enabled inherited footer, date, and slide-number fields are recorded as unsupported and fail the required rendition rather than disappearing silently. SmartArt source identity and text survive the retained fixture, but visual fidelity against PowerPoint remains unverified. Polling output-size checks are guardrails, not an exact disk quota. See [LIMITATIONS.md](LIMITATIONS.md).
