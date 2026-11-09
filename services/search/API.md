# Search service API

All routes except `GET /health` require `Authorization: Bearer <SEARCH_TOKEN>`. The service returns JSON and does not log query or slide text. Request sizes and text fields are bounded by the Pydantic request models and the configured tokenizer limits.

| Route | Request | Success | Errors |
| --- | --- | --- | --- |
| `GET /health` | None | `200` with `live`, `ready`, and a safe error code | Always responds while the process is alive |
| `GET /v1/policy` | None | `200` with active profile, model label, processing location, and content disclosure | `401`, `503` |
| `POST /v1/index-jobs` | `Idempotency-Key`; library, deck, generation, source and index revisions, profile, ordered slide text | `202` with job ID and status URL | `400`, `401`, `409`, `410`, `413`, `503` |
| `GET /v1/index-jobs/{job_id}` | None | `200` with state, segment progress, eligible/searchable/blank/hidden slide counts, warnings, and safe error | `401`, `404`, `503` |
| `POST /v1/index-jobs/{job_id}/retry` | `Idempotency-Key` | `202` with the existing job ID | `401`, `404`, `409`, `410`, `503` |
| `POST /v1/search` | Library, active profile, query, optional deck, result limit and continuation offset | `200` with ranked deck groups and slide evidence | `400`, `401`, `409`, `413`, `503` |
| `DELETE /v1/decks/{deck_id}?library_id={uuid}` | Library identity | `200` after the permanent fence and derivative cleanup commit | `401`, `503` |

Index retries preserve the original snapshot, profile, and index revision. A repeated key and identical payload returns the same job. A repeated key with different input returns `409`. A later revision supersedes older work. A permanent deletion fence rejects later submissions, including submissions for UUIDs not previously indexed.

Search responses label `mode` as `hybrid` or `lexical`, set `degraded` when embedding inference failed, and return eligible, searchable, blank, and hidden slide counts. Each slide result includes its generation and index revision, ranked score kind, matching path, native or notes channel, plain-text snippet, and extraction warning metadata. Scores are ranking values, not confidence estimates. The library must validate every identity against current metadata and create asset URLs itself.

Search errors use `{ "error": { "code": "...", "message": "...", "retryable": false } }`. Database failures return `503`; they never become an empty success. Query inference failures return labelled lexical results only after a corpus profile is ready. Invalid model identity, invalid vector shape, and unsafe configuration never publish an index.
