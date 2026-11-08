# Library HTTP API v1

Content routes require `Authorization: Bearer <LIBRARY_TOKEN>`. `/health` is public and reports ready only while the worker runs and the selected database responds. Authentication precedes request-body reads. Responses use `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and a UUID `X-Request-ID`. Public OpenAPI routes are disabled. `create_app(...).openapi()` remains available for local contract checks.

| Method and path | Request | Success |
| --- | --- | --- |
| `GET /health` | None | 200 `{"ready": true}` when the worker and database are available; otherwise `{"ready": false}` |
| `POST /v1/decks` | Multipart `file` exactly once; `Idempotency-Key` header | 202 `Accepted` |
| `GET /v1/decks` | None | 200 array of active `Deck` records, newest first |
| `GET /v1/deletions` | None | 200 minimal deletion fence records, newest first |
| `GET /v1/decks/{deck_id}` | None | 200 `Deck`, including deletion state |
| `GET /v1/operations/{operation_id}` | None | 200 stable `Operation` record |
| `POST /v1/decks/{deck_id}/retry` | Empty body; `Idempotency-Key` header | 202 `Accepted` |
| `POST /v1/decks/{deck_id}/reprocess` | Empty body; `Idempotency-Key` header | 202 `Accepted` |
| `GET /v1/decks/{deck_id}/manifest` | Optional UUID `generation` query | 200 validated extraction `Manifest` snapshot |
| `GET /v1/decks/{deck_id}/original` | None | 200 canonical `.pptx` bytes |
| `GET /v1/decks/{deck_id}/generations/{generation_id}/assets/{asset_id}` | None | 200 authenticated asset bytes |
| `DELETE /v1/decks/{deck_id}` | None | 202 `Accepted` for durable cleanup or a deletion fence for an unknown UUID |
| `GET /v1/decks/{deck_id}/deletion` | None | 200 `Deck` in `deleting` or `deleted` state |

All identifiers are UUIDs. Keys contain 1 to 128 ASCII letters, digits, underscore, period, colon, or hyphen. Upload filenames must end in `.pptx`. Both slash styles are stripped to their basename. Empty files, control characters, and names longer than 255 characters are rejected. Extraction validates the actual OOXML content asynchronously; a library upload acknowledgment does not certify valid presentation content.

## Accepted work and status

`Accepted` contains `deck_id`, `operation_id`, `status_url`, and `operation_url`. Mutation responses retain stable operation identifiers. Repeating a keyed upload with identical filename and bytes returns the original accepted operation. Reusing its key with different input returns 409. Retry and reprocess keys bind the action and deck. A repeated key returns its original operation, even after a newer one exists. Keys are global within this library.

`Deck` contains `deck_id`, nullable `filename`, nullable `source_sha256`, nullable `byte_length`, `source_revision`, `state`, `operation_id`, nullable `generation_id`, `completed_slides`, `total_slides`, `stage`, nullable `error`, `warnings`, `status_url`, nullable `manifest_url`, nullable `original_url`, and `updated_at`.

States are `queued`, `processing`, `extracted`, `failed`, `deleting`, and `deleted`. `extracted` means that a complete extraction generation is available. It does not mean searchable indexing has finished. Poll deck or operation status once per second, then back off for long work. Partial processing cannot publish a manifest.

`Operation` contains `operation_id`, `deck_id`, `kind` of `extract`, `retry`, or `delete`, `state` of `pending`, `processing`, `succeeded`, `failed`, or `cancelled`, `phase`, `source_revision`, progress counts, `stage`, `error`, `warnings`, timestamps, and its `status_url`. Previously accepted operation IDs remain observable after supersession.

Retry accepts only the current failed operation with `error.retryable=true`. It preserves the revision. A failed extraction job uses extraction's keyed retry endpoint. A transport failure resumes the same recorded submission or polling request, so an uncertain response cannot duplicate attempts. Reprocessing increments the revision and submits the same immutable original with a new stable request key. Both operations retain the previous complete generation until a replacement is validated.

## Manifests and downloads

The manifest follows [extraction API v1](../powerpoint-extraction/API.md). The library independently validates its schema, deck, revision, generation, source checksum, safe filename, asset references, rendition roles, and warning references before publication. `canonical_original_reference` becomes the library original-download route. Every asset `download_url` becomes a generation-bound library route. Extraction service credentials and private paths are absent.

One response describes one complete generation. An old generation selector returns 404 after library replacement. Extraction can retire an old generation just before library status updates, so an old asset download may return a conflict. Refresh the complete deck and manifest in that case. Do not combine old text and new images.

Downloads return attachments with a UTF-8 safe filename, media type, byte length, and quoted SHA-256 ETag. Graphic filenames include source filename, slide number, export role, and opaque asset ID. Original bytes match the upload checksum. Asset bytes are bounded and checked against their manifest length, media type, ETag, and SHA-256. The library rechecks its deletion and generation fence after receiving upstream bytes and before returning them. Bytes already authorized for an HTTP response can finish transmission after deletion begins.

Assets retain `slide_preview`, `original_picture`, `isolated_render`, `group_render`, and `contextual_crop` roles. Original embedded pictures and rendered versions are separate assets. A contextual crop must be labelled as a slide crop and show its warning before download. It can contain background and overlapping objects. Unsafe original formats, including SVG, are attachments and must not be inserted as inline page content.

## Deletion and recovery

Deletion first commits a permanent fence, revokes manifest and download access, cancels unfinished library operations, and hides the deck from listing. Cleanup removes the canonical original independently of extraction availability, then repeats extraction deletion until it reports complete. Startup resumes unfinished operations. A late worker response cannot change a fenced deck or publish a stale revision.

Repeated deletion returns the same accepted deletion operation. Deleting an unknown UUID creates a minimal fence and asks extraction to delete that UUID, so a post-snapshot deletion can be safely replayed after restoring an older backup. Deleted UUIDs cannot be reused. The fence transaction removes filenames, checksums, manifests, remote job records, and warnings from metadata. Cleanup removes original bytes. Minimal identity, deletion-fence, and operation status records remain. Upload-key replay against a deleted deck returns 410. Backups have separate administrator retention as described in the README.

`GET /v1/deletions` returns only `deck_id`, `state`, `operation_id`, and `updated_at`. It returns no filename, checksum, slide text, or asset reference. Export this list with each backup so a newer deletion can be replayed after restoring an older snapshot.

## Errors and limits

Errors have this shape:

```json
{"error":{"code":"extraction_unavailable","message":"Cannot reach extraction. Check its URL, readiness, and service token, then retry.","retryable":true,"stage":null},"request_id":"00000000-0000-0000-0000-000000000000"}
```

| Status | Meaning |
| --- | --- |
| 400 | Malformed UUID, key, fields, filename, query, body, or body timeout |
| 401 | Missing or incorrect library token |
| 404 | Unknown deck, operation, asset, manifest, or generation |
| 409 | Conflicting request key, invalid retry, revision limit, or stale upstream asset |
| 410 | Deleted deck identity |
| 413 | Complete request or uploaded file exceeds its configured limit |
| 415 | Unsupported upload format |
| 422 | Empty upload |
| 503 | Canonical original unavailable, temporary upstream download failure, invalid persisted metadata, or library storage unavailable |

Accepted processing failures appear in a 200 status response with a safe structured error. No traceback, credentials, internal endpoint, private path, or slide text appears in errors. Five consecutive transient processing failures exhaust automatic retries and leave a retryable failed operation. An extraction 429 keeps work queued and waits for capacity with capped backoff. Delete operations retain their fence and retry cleanup indefinitely with capped backoff. Default limits and timeout values are in the README.

Database errors use HTTP 503 with code `storage_unavailable` and a restart instruction. The service does not replay a mutation after a connection loss because PostgreSQL may have committed it before the response was lost. Repeat the request with its original idempotency key after restart, or poll the operation URL returned by the original request. Storage credentials and connection details never appear in HTTP responses.

`LIBRARY_DB_BACKEND` selects `sqlite` or `postgresql` at startup and defaults to `sqlite`. `LIBRARY_POSTGRES_DSN` is required only for PostgreSQL and is not part of the HTTP API. Both backends keep originals, incoming uploads, the root marker, and the owner lock under `LIBRARY_DATA_DIR`. See the [library README](README.md#use-postgresql) for setup and transfer commands.
