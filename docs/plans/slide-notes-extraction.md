# Slide notes extraction plan

## Goal

Extract PowerPoint speaker notes as slide-level text in each completed extraction manifest. Include notes by default and keep them in a separate text channel associated with their slide for indexing.

This plan covers extraction, policy, manifest transfer, and reprocessing. The extraction contract supplies notes for indexing by default. It does not display notes in the gallery, render notes pages, or extract comments, audio, or video.

## Project fit

The constitution requires local processing by default, safe handling of presentation content, complete immutable generations, retry safety, and removal of derived content on deck deletion. This feature keeps those rules.

Speaker notes are included by default and indexed as a separate text channel associated with each slide. An administrator may disable extraction through explicit configuration. The current application has one shared library and no tenant policy boundary, so use one deployment-wide administrator setting owned by the library service. Expose the active setting on the upload screen and pass it to extraction with each job. Per-tenant control would require a separate product and access-control decision.

Note text becomes part of the authenticated manifest returned by the library. All users with access to the shared library can read it. Disabling extraction does not remove notes from the preserved original deck or its original-deck download.

## Data shape

Add an optional `notes` value to each slide's extracted content. Keep it outside `source`, `occurrences`, renditions, and assets.

The value records one of three states and a truncation flag:

- `present` with the speaker-note paragraphs in order and `truncated` set when the text cap removed content.
- `empty` with no paragraphs when note extraction is enabled and the slide has no non-empty speaker-note text.
- `excluded` with no paragraphs when the configured policy omits notes.

Accept a missing or null `notes` value as a legacy manifest created before this feature. Do not interpret a legacy value as proof that the slide has no notes.

Preserve paragraph order and Unicode. Normalize XML line endings, but do not merge paragraph boundaries. Do not include notes-master placeholders, slide images, page numbers, dates, headers, footers, or comments.

## Extraction and publication

1. Read only the speaker-notes body associated with each slide. Use the supported presentation-library notes accessor if fixture checks prove it returns body text without notes-master boilerplate. Otherwise, follow the slide's internal OOXML relationship to its notes part and read only the body placeholder. Never render the notes page.
2. Apply the deployment-wide policy at submission. The library service reports its setting through an authenticated policy route and sends the selected value in the extraction request. Default to included. Record the value with the job so a queued job or retry uses the same choice after a restart or configuration change.
3. Add the notes value to the extraction service's slide content contract and the library's independently validated contract. Keep extraction schema version 1 because the field is optional and additive. Existing manifests remain valid.
4. Include the notes policy, parser revision, and text cap in the extraction configuration fingerprint. A generation must identify whether notes were included or excluded.
5. Bound note text per slide using a configurable limit that fits the manifest size limit. Truncate deterministically and set `truncated`; never cut content silently. Fail the generation on malformed notes data.
6. Publish the complete manifest through the existing library validation and generation swap.
7. Reprocess existing decks through the existing per-deck reprocess route. It increments the source revision and submits the preserved original again. The old complete generation remains active if the replacement fails. No bulk reprocessing endpoint is part of this feature.
8. Keep notes out of logs and errors. Existing deletion must revoke notes with the manifest and prevent a late job from restoring them.

## Affected modules and files

- `services/powerpoint-extraction/src/powerpoint_extraction/source.py` reads note paragraphs into the source inventory.
- `services/powerpoint-extraction/src/powerpoint_extraction/content.py` and `renderer.py` carry notes into the slide content without creating renderer objects or assets.
- `services/powerpoint-extraction/src/powerpoint_extraction/models.py` and `store.py` validate and persist the new field and job policy.
- `services/powerpoint-extraction/src/powerpoint_extraction/api.py`, `API.md`, and `README.md` accept and document the per-job policy and text cap.
- `services/powerpoint-extraction/tests/` and fixture documentation cover extraction behavior, response shape, policy, and limits.
- `services/library/src/library_service/models.py`, `api.py`, `extraction.py`, and `API.md` configure and report the policy, send it with extraction requests, and validate the same manifest field.
- `services/library/tests/test_library.py` proves the service-to-service contract, legacy compatibility, reprocessing, and deletion behavior.
- `frontend/src/client.ts` reads the policy and `frontend/src/main.ts` reports it before upload. The gallery does not display note text. The search index consumes the separate notes channel by default.
- `docs/plans/powerpoint-extraction.md` links to this plan and no longer lists speaker notes as deferred work.

## Work sequence

1. Build a sanitized fixture with notes on multiple slides, multiple paragraphs, Unicode, empty notes, no notes part, a long note that reaches the text cap, a hidden slide, notes-master placeholders, and comments. Confirm the selected notes accessor or bounded OOXML path returns only speaker text and preserves slide order.
2. Add the optional typed notes value and immutable per-job policy capture. Keep notes outside renderer and asset handling.
3. Update extraction and library schemas, API contracts, configuration docs, and the upload policy notice in the same change.
4. Prove a new upload and a reprocessed existing deck publish notes under the default policy and report `excluded` states when an administrator disables extraction.
5. Prove old stored manifests remain readable, duplicate submissions reuse the correct job, retries retain their captured policy, failed replacement preserves the previous generation, and deletion removes notes with all other derived content.
6. Run the extraction and library checks, then exercise the upload and reprocess flows through the running local services. Save manifests and screenshots without retaining private production notes.

## Acceptance criteria

- Each slide's note paragraphs stay associated with the correct slide, including hidden slides.
- Text, Unicode, paragraph boundaries, and empty-note state survive extraction and library publication.
- The default policy records note text in the manifest and identifies the policy and text cap in the extraction fingerprint.
- Each new manifest exposes note text as a separate channel so indexing can use it without merging it with visible slide text.
- An administrator exclusion policy publishes no note text and reports `excluded` for every slide.
- The upload screen shows the active policy before upload.
- Existing manifests without `notes` remain readable. Invalid new note data fails closed without exposing its contents.
- Reprocessing the same original creates a new complete generation with notes. It does not alter the source file checksum.
- A failed reprocess leaves the previous generation available. Repeated submission and retry do not change the captured policy or duplicate slide content.
- Deletion revokes note content, and a cancelled or stale job cannot publish it later.
- No note text appears in logs, error messages, renderer assets, or gallery content.

## Scope boundary

Use the deployment-wide policy for the current shared library. Do not add per-tenant controls without first defining tenant identity and access checks across upload, manifest reads, search, and deletion.
