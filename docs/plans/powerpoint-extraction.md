# PowerPoint extraction plan

Create searchable slide content and downloadable slide and graphic images for the shared team library.
Run on Windows and Linux without requiring Microsoft PowerPoint or cloud processing.
Use a Python extraction library together with headless LibreOffice, subject to the rendering proof below.
Deliver EX1, the extraction service, followed by EX2, the library integration and downloads.
EX1 implementation is present under `services/powerpoint-extraction/`. Renderer and live HTTP evidence exists for selected fixtures and workflows. EX1 remains open because source parsing is not confined, native Windows confinement and inherited dynamic fields are unsupported, SmartArt fidelity is provisional, and representative capacity is not measured. EX2 is out of scope for the current implementation request.

## How to read this

One box is one unit of work. Every box names the evidence that checks it. Check a box only when its evidence exists.
Evidence can be a fixture, API response, image, test result, or recorded measurement.
The body is a how-to. The appendices explain decisions and define the proposed data contract.
Use `skills/poteto-mode/playbooks/autopilot-stack.md` from the installed pstack plugin if execution is later requested as a PR stack.
This plan does not authorize commits, pushes, PR creation, or merges. The operator retains merge authority.
Follow repository instructions over playbook defaults, particularly baseline measurement before numerical performance promises.

Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

## Program checklist

### Arm the program

- [ ] Begin implementation only when requested. Record this plan and the done condition in the standing orders.
- [ ] Read the installed plugin execution playbook and the project constitution. Record any changed requirement before coding.
- [ ] Resolve the renderer proof in EX1 before committing to object exports. Save actual PNGs and manifests on both operating systems.
- [ ] If execution becomes unattended, schedule an hourly audit using an available scheduler. Otherwise mark scheduling inapplicable and audit at each phase boundary.
- [ ] Send a status message when evidence changes a decision, a phase completes, or a blocker appears.
- [ ] On a stop request, halt writers and record completed work and outstanding evidence.

### Spawn owners

- [ ] Assign EX1 and EX2 to sequential owners. Use a single owner if delegation does not improve this small repository.
- [ ] Start EX2 after EX1 has a verified API contract. In a requested stack, base EX2 on the EX1 branch.
- [ ] Keep extraction implementation, persistence, tests, dependencies, fixtures, and configuration in `services/powerpoint-extraction/`.
- [ ] Put library ownership in `services/library/` when implementing EX2. Put TypeScript client code and tests in `frontend/`.
- [ ] Create each module only when implementing it. Create no placeholder services.
- [ ] Treat EX2 as interaction-changing. Gather review images and a video before any proposed merge.

### PR mechanics, for every PR

- [ ] Apply repository checks and inspect affected callers, fixtures, contracts, configuration, and deployment instructions.
- [ ] If a PR is requested, resolve the repository's forge and base branch without assuming either exists.
- [ ] Read the relevant pstack implementation and review skills before their steps. Do not introduce unsupported tooling solely for this plan.
- [ ] Record exact revisions and retained evidence for each review. Do not treat this plan as authorization to publish.

### Verdict and merge, for every PR

- [ ] Execute the scenarios below on the actual service and application. Record failures and limitations.
- [ ] Check Windows and Linux evidence independently. A Windows-only result cannot establish Linux support.
- [ ] Require an independent review before declaring a PR merge-ready when using the PR playbook.
- [ ] Leave merges to the operator. Stop after the requested planning deliverable in this session.

### Boot recipe, for every live lane

- [ ] At implementation time, record the exact install, start, and check commands in each module README before live verification.
- [ ] Start EX1 without other application services. For EX2, start the library service, extraction service, and frontend using their documented commands.
- [ ] Supply inputs through HTTP or the frontend. Do not use another service's database or private filesystem as an integration shortcut.
- [ ] Use an authenticated HTTP test client for service scenarios and the available browser driver for frontend scenarios.
- [ ] Save API responses, manifests, checksums, screenshots, renderer versions, operating system details, and logs under a per-run evidence directory.
- [ ] For API lanes, save a screenshot of the response or rendered artifact alongside machine-readable evidence. Screenshot evidence alone is insufficient.

## Build extraction and prove rendering coverage (EX1)

**Depends on.** None.

**Files.**

- [x] Create the extraction module README, dependency manifest, API contract, implementation, tests, and fixtures under `services/powerpoint-extraction/`.
- [x] Keep executable probes and their repeatable commands in the extraction module.
- [x] Update this plan with selected versions, measured limits, and prototype evidence.

**Build.**

- [x] First create representative decks with ordinary text, tables, chart labels, pictures, cropped pictures, transparency, rotation, nested groups, connectors, SmartArt, master content, hidden slides, and repeated image bytes.
- [x] Compare library extraction plus LibreOffice rendering with LibreOffice-only extraction. Use the same fixtures and save all outputs.
- [x] Prove page export, isolated shape export, group export, alpha behavior, and source-to-renderer object mapping on Windows and Linux.
- [x] Detect source elements lost or rewritten by LibreOffice. Do not infer coverage from a successful process exit.
- [x] For unmapped objects, prove the labelled slide-crop fallback using source geometry and the actual rendered slide dimensions.
- [x] If the imported slide itself loses content, report a failed required rendition. A crop cannot recover missing content.
- [x] Finalize the provisional data model in Appendix B with runtime-validated request and response schemas.
- [x] Extract text and original image bytes using public library APIs where supported. Use bounded OOXML inspection for required properties not exposed by the library.
- [ ] Render each slide and visible drawable object, including grouped and inherited content. Preserve hierarchy without inventing semantic groups from nearby shapes. Enabled inherited date, footer, and slide-number fields remain unsupported and fail the required slide rendition. See `services/powerpoint-extraction/LIMITATIONS.md`.
- [x] Export all children and the parent of an explicit group. Give the parent priority in default browsing to avoid presenting repeated pieces as separate primary graphics.
- [x] Distinguish original files, isolated object images, and contextual crops in metadata and download labels.
- [x] Implement durable jobs, restart recovery, retry, immutable output generations, and deletion fencing as defined in Appendix B.
- [x] Implement resource limits, authentication boundaries, private file storage, safe archive handling, macro suppression, and disabled external content fetching. Source package parsing remains in the service process; only Linux LibreOffice conversion is OS-confined. Native Windows rendering remains disabled because confinement is not proven there. See `services/powerpoint-extraction/LIMITATIONS.md`.
- [x] Run one conversion at a time initially with an isolated LibreOffice profile and temporary directory. Terminate timed-out worker processes and clean their temporary files.
- [x] Keep LibreOffice inside this service as a local worker integration. Use its available Python UNO bridge without creating another application service.
- [x] Define requests, responses, errors, authentication, retention, and independent start commands in the module API contract and README.

**You see.**

- [x] A valid upload returns a job immediately. Polling eventually exposes a complete manifest with slide previews and graphic downloads.
- [x] Original picture downloads match the embedded bytes. Rendered downloads identify their method and any warnings.
- [x] Unsupported exports produce actionable failures or explicitly allowed crop warnings. No source object silently disappears.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [x] Add fixture-based checks for text, image bytes, occurrence identity, transforms, groups, hidden slides, and rendition coverage.
- [x] Add focused lifecycle checks for duplicate submission, retry, restart recovery, deletion during conversion, and stale generation publication.
- [x] Check API schema validation, inaccessible assets, malformed archives, size limits, process failure, timeout, and missing renderer dependencies.
- [x] Run the extraction module's documented test and type-check commands. Save actual commands and exit codes in `services/powerpoint-extraction/PROOF.md`.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on the configured `swarm workers` model at the PR head, when running the pstack PR workflow. These are scenarios, not a requirement for ten simultaneous processes.

- [ ] Lane 1. Regression lane against trunk. Record that trunk has no extraction feature, then upload a simple deck at head. Save `ex1-simple.png`. Pass when the original is unchanged and every slide has text metadata and a PNG.
- [ ] Lane 2. Extract the crop, rotation, and transparency fixture. Save `ex1-picture.png`. Pass when original bytes match and rendered occurrences preserve the tested appearance or carry a truthful warning.
- [ ] Lane 3. Render charts, tables, and SmartArt. Save `ex1-complex.png`. Pass when each source object has an isolated export or an explicitly labelled contextual crop.
- [ ] Lane 4. Render nested groups and connectors. Save `ex1-groups.png`. Pass when hierarchy, geometry, and group exports match the fixture.
- [ ] Lane 5. Extract hidden slides and inherited master content. Save `ex1-inherited.png`. Pass when numbering is preserved and hidden status and visible inherited objects are represented.
- [ ] Lane 6. Reuse one embedded picture in multiple positions and groups. Save `ex1-reuse.png`. Pass when each occurrence has its own metadata and renditions reference the correct source asset.
- [ ] Lane 7. Crash and retry a conversion. Save `ex1-retry.png`. Pass when the job recovers or fails actionably and retry produces one published generation.
- [ ] Lane 8. Delete during conversion, then repeat a stale submission. Save `ex1-delete.png`. Pass when no deleted deck bytes or artifacts reappear.
- [ ] Lane 9. Supply malformed, oversized, external-link, and unsupported-content fixtures. Save `ex1-errors.png`. Pass when limits and failures are actionable and no external content is fetched.
- [ ] Lane 10. Run the representative set on both Windows and Linux. Save `ex1-platforms.png`. Pass when slide counts, source checksums, manifest invariants, and approved visual examples pass on each platform.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [x] Metric. Measure per-deck wall time, sampled worker memory, output bytes, timeout counts, and queue observations. Include a 20-slide fixture and 100-deck serial set. See `services/powerpoint-extraction/evidence/capacity-20261105/README.md`; memory and queue figures are sampled, not exact peaks.
- [ ] Probe. Record identical fixtures and cold and warm conditions for repeated runs. Compare both candidate extraction approaches where both perform the same work. The retained run used identical fixtures but did not flush the OS page cache or run a comparative LibreOffice-only workflow.
- [x] Baseline. Record that trunk has no comparable conversion path. Measure candidate baseline first. Do not calculate a trunk speedup.
- [ ] Rule. Before enabling production processing, use measured evidence to set documented file, archive, image, memory, queue, and time limits. Adopt numerical service targets only after baseline review, as the constitution requires. Failure to complete within those selected limits fails this phase. Current guardrails predate the single-host run and are not production targets.

**Review gate.** None. EX1 is not review-gated for a frontend interaction. Review the actual exported images and contract as technical evidence.

**Merge.**

- [ ] Record exact head, passing checks, rendering evidence, and outstanding limitations.
- [ ] If a PR was requested, finish independent review and hand it to the operator. Otherwise retain local changes only.

## Connect library processing and graphic downloads (EX2)

**Depends on.** EX1.

**Files.**

- [ ] Create the library service's implementation, tests, API contract, and README in `services/library/`.
- [ ] Create the upload, status, preview, and download client code and checks in `frontend/`.
- [ ] Add repository-wide local orchestration only when needed. Document service URLs and required local dependencies.

**Build.**

- [ ] Preserve canonical uploaded decks in the library service. Supply extraction with deck bytes and stable identity over authenticated HTTP.
- [ ] Keep extraction's work copy private. Document its retention and deletion along with all derived outputs.
- [ ] Poll extraction jobs with outbound timeouts and bounded retries for safe operations. Distinguish extraction completion from full indexing readiness.
- [ ] Make current generation manifests and asset bytes available through service APIs. Expose no private paths and do not send full image bytes in JSON manifests.
- [ ] Add original-deck, slide PNG, original-picture, and rendered-graphic downloads. Show source filename, slide number, export type, and warnings.
- [ ] Display contextual crops with a warning that surrounding objects or slide background may be included. Do not label those images as isolated graphics.
- [ ] Show text and visual content to the future indexing consumer through the manifest API. Leave embedding and retrieval implementation to a separate feature plan.
- [ ] Coordinate reprocessing and deletion across the library and extraction services using durable operations and generation checks.
- [ ] Keep a deck in a processing state until all required configured indexing steps finish. In this slice, label completed extraction as extracted rather than implying searchable readiness.
- [ ] Add a keyboard-accessible upload and download flow with labelled controls and readable failures.
- [ ] Document backup and restore of canonical originals, extraction metadata, derived outputs, deletion fences, and pending operations.

**You see.**

- [ ] A user uploads a deck, follows processing status, and downloads the original, a slide image, and both available picture forms.
- [ ] Graphic downloads retain source attribution and reveal crop warnings before download.
- [ ] Deleting a deck prevents future downloads and removes every active copy and derivative.

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Check the library-to-extraction contract for submission, polling, failures, downloads, supersession, and deletion.
- [ ] Check the TypeScript client's runtime validation, status presentation, warning labels, and keyboard operation.
- [ ] Check deletion retries, stale consumer writes, partial service outages, and downloads attempted after deletion.
- [ ] Run each affected module's documented checks and save the results.

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked. Ten lanes on the configured `swarm workers` model at the PR head, when running the pstack PR workflow. Run the full workflow locally.

- [ ] Lane 1. Regression lane against trunk. Record that trunk lacks the workflow, then upload and download at head. Save `ex2-workflow.png`. Pass when checksums match and slide attribution is correct.
- [ ] Lane 2. Download original and rendered forms of a cropped picture. Save `ex2-picture-downloads.png`. Pass when the user can distinguish both and receives the expected bytes.
- [ ] Lane 3. Download a chart, SmartArt graphic, and explicit group. Save `ex2-graphics.png`. Pass when each available export has the correct source slide and export method.
- [ ] Lane 4. Trigger an approved contextual-crop fallback. Save `ex2-crop-warning.png`. Pass when the warning is visible before download and remains in manifest metadata.
- [ ] Lane 5. Trigger extraction failure and retry through the application. Save `ex2-retry.png`. Pass when the error is actionable and retry does not duplicate results.
- [ ] Lane 6. Delete during extraction and restart services. Save `ex2-delete.png`. Pass when downloads remain unavailable and active files are removed after cleanup completes.
- [ ] Lane 7. Reprocess while viewing an older generation. Save `ex2-generation.png`. Pass when all displayed assets belong to one complete generation and stale writes cannot replace newer results.
- [ ] Lane 8. Stop extraction during a library request. Save `ex2-outage.png`. Pass when the application shows a bounded failure and durable retries converge after restart.
- [ ] Lane 9. Use keyboard-only navigation and an unauthenticated client. Save `ex2-access.png`. Pass when controls are usable and all content endpoints deny unauthorized access.
- [ ] Lane 10. Run local Windows and Linux workflows without network egress. Save `ex2-local.png`. Pass when no cloud service is required and every required download succeeds.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.

- [ ] Metric. Measure upload-to-extracted time, polling traffic, download time, deletion completion, disk growth, and peak memory for the initial 100-deck capacity.
- [ ] Probe. Use the same fixture set and hardware for repeated EX1-only and complete EX2 workflow runs. Identify HTTP transfer and orchestration costs separately.
- [ ] Baseline. Measure EX1 first. Record that trunk has no complete workflow, so a trunk-to-head end-to-end ratio is unavailable.
- [ ] Rule. Record capacity behavior and select operational limits from measured baselines before deployment. Fail on unbounded growth, exceeded selected limits, missing outputs, or content recreation after deletion. Make no unmeasured latency promise.

**Review gate.** The operator reviews before any proposed merge.

- [ ] Save screenshots for uploads, completed previews, downloads, warnings, and failures to the review evidence directory.
- [ ] Save a video showing upload, preview, graphic download, and deletion.
- [ ] Present screenshots and video to the operator if a merge is requested. This planning session does not request implementation or merge approval.

**Merge.**

- [ ] Record the tested extraction API version and exact revisions of all affected modules.
- [ ] Resolve independent review findings and hand any requested PR to the operator.

## Close the program

- [ ] Check every acceptance item using retained evidence. Record any missing operating-system or presentation-content coverage.
- [ ] Publish module setup instructions and list all unsupported presentation features.
- [ ] Verify original-deck checksums and active-library deletion across every implemented service.
- [ ] Keep search quality and GPU/model capacity acceptance in the later indexing and search feature. Do not claim those pass from extraction tests.
- [ ] Report completed work, evidence paths, measured limits, and remaining risks.

## Appendix A. Prototype evidence

The EX1 implementation decision is source-first: bounded OOXML and python-pptx inspection supplies stable source identity and original media bytes, while LibreOffice supplies actual slide and shape renders. The retained UNO-only report is compared against the same source fixture. This keeps lost imports detectable and gives crops source geometry when an isolated export fails.

The Windows and Linux renderer versions, fixture checksums, actual PNGs, and source-to-renderer mapping outcomes are recorded in `services/powerpoint-extraction/PROOF.md` and `services/powerpoint-extraction/evidence/`. The ordinary fixture has 14 occurrences and zero mapping/text failures on both platforms. The genuine SmartArt fixture maps and exports on both, but visual fidelity remains provisional without a PowerPoint reference.

The validated Python dependency versions are pinned in `services/powerpoint-extraction/uv.lock`; LibreOffice and OS confinement dependencies are external. Limits are guardrails and are listed in `services/powerpoint-extraction/LIMITATIONS.md`. No 20-slide or 100-deck capacity measurement is claimed. Native Windows AppContainer cannot connect to LibreOffice's UNO pipe, so the native Windows renderer remains unavailable.

| Runtime | Verified version |
| --- | --- |
| Windows Python | 3.12.11 |
| Windows LibreOffice | 26.8.2.2 |
| Linux Python | 3.12.3 |
| Linux LibreOffice | 24.2.7.2 |
| Python packages | Exact versions and hashes in `services/powerpoint-extraction/uv.lock` |

Context7 resolution and documentation retrieval succeeded for python-pptx and the LibreOffice developer guide on 2026-11-01.
PowerShell blocked the initial npx.ps1 wrapper. Calling npx.cmd succeeded without changing execution policy.
Documentation establishes candidate APIs, not fidelity or source-object mapping.
EX1 begins with the unresolved empirical questions. Do not treat this plan as a completed feasibility proof.

## Appendix B. Scope, architecture, and proposed data contract

### Accepted product decisions

The project owner requested both internal extraction outputs and user-facing downloads.
Pictures, charts, SmartArt, tables, groups, connectors, and other drawable slide objects must be represented as images.
Retain original embedded picture bytes and a rendered version for each occurrence.
Original picture bytes mean the media file stored in the deck, not an earlier uncompressed source file.
Generated charts and SmartArt usually have no original standalone picture. Mark original-image availability explicitly.
Close visual similarity to PowerPoint is acceptable. Pixel-identical rendering is not required.
Allow a clearly labelled slide crop when isolated graphic export fails or object mapping is uncertain.
A crop can include background and overlapping elements. Never imply it preserves transparency or isolation.

Extract native text, table cells, chart titles and labels where available, and alternative text.
Include hidden slides with a hidden flag and their original slide numbers.
Exclude hidden slides from default search results while retaining their downloadable artifacts.
Defer speaker notes, OCR, font styling reconstruction, editable chart data, animation playback, and video or audio extraction.
Retain the original deck for editable reuse. Export a static appearance for animated objects.
The default export is PNG. Preserve original embedded formats as separate downloadable files.
Use transparent isolated object exports when supported. Full-slide PNGs retain their rendered slide background.
The proposed default slide image width is 1920 pixels with aspect ratio preserved.
Start object exports at the same slide-coordinate scale. Cap pixel area and record dimensions. Adjust defaults after the probe.
Do not add ZIP bundles, automatic diagram grouping, or an optional PowerPoint backend in the first implementation.

### Module ownership and movement of files

The extraction service owns its jobs, manifests, derived assets, work copies, temporary directories, and SQLite metadata database.
SQLite and private local asset files are a proposed initial persistence choice, not a project-wide database decision.
The library service owns canonical original decks, user-facing library lifecycle, and durable orchestration operations.
The frontend uses library HTTP APIs. The library service calls extraction HTTP APIs with configured credentials and timeouts.
The library sends the original bytes with deck identity and a monotonic source revision. No private source path crosses the API.
The extraction service may retain one private work copy for retry and deletes it when superseded or the deck is deleted.
Document every copy in backup and deletion instructions.

LibreOffice UNO is a local integration inside extraction, not a second application module.
This uses LibreOffice's native interface instead of REST because it is the renderer's supported local automation mechanism.
Keep UNO reachable only through a private local transport. Do not expose it as a network service.
This is the documented local-tool communication exception. All application-module communication remains REST.

### Identity and geometry

Use a library-assigned immutable deck UUID. A deleted UUID cannot be reused.
Give every source revision an increasing integer and every extraction attempt its own job ID.
Identify slides by deck ID, source revision, and source slide ID. Retain the one-based slide number for users.
Identify elements by source part, source shape ID, and group path. Record inherited element occurrences separately per slide.
Do not assume source shape IDs equal LibreOffice object IDs.
Record the observed renderer mapping and its method. Use contextual crops when isolated mapping cannot be proven.
Store source geometry in integer English Metric Units and rendered dimensions in pixels.
For nested groups retain local geometry and a transform to slide coordinates, including rotation, flips, and scale.
Use an affine transform and a slide-space bounding rectangle. Compute crops from transformed bounds and clip to the slide.
Retain stacking order separately from text reading order. Geometry alone does not establish semantic reading order.
Permit unknown reading order rather than inventing it.

### Records

| Record | Required information |
| --- | --- |
| Deck reference | Deck ID, source revision, filename, original checksum, slide count, source dimensions, canonical original reference owned by the library |
| Extraction generation | Generation ID, deck identity, schema version, extractor and renderer versions, configuration fingerprint, timestamps, counts, warnings, published status |
| Slide | Slide key, source slide ID, slide number, title when known, dimensions, hidden flag, plain text, preview asset reference, ordered element references |
| Element occurrence | Element key, slide key, source kind, group parent, source part and shape ID, source scope of slide or layout or master, name, alternative text, geometry, transforms, stacking order, text blocks, asset references, export outcome |
| Text block | Plain text, paragraph boundaries, source element key, source kind, optional language and reading order, table cell coordinates where applicable |
| Binary asset | Opaque asset ID, owning deck and generation, role, media type, byte length, checksum, dimensions when applicable, owning service's download link |
| Rendition | Element or slide key, asset ID, method of slide render or isolated render or contextual crop, renderer version, pixel size, transparency flag, source crop rectangle where applicable, warnings |
| Extraction job | Job ID, deck ID, source revision, generation ID, state, stage, attempt, completed and total slide counts, timestamps, error, warning list |
| Deletion fence | Deck ID, deletion state and timestamp, rejected source revisions, cleanup progress, last cleanup failure without content or credentials |

Keep records together in a versioned manifest. This table does not require one database table per record.
Retain original-image asset links separately from rendered-rendition links.
Reference a shared source asset for repeated embedded bytes within one deck, but keep each occurrence distinct.
Keep byte deduplication within one deck initially to simplify deletion. Do not merge user uploads by filename or checksum.
For unknown shape kinds, preserve source identity and bounds and attempt a crop. Record the unsupported kind.
Include master and layout elements visible on the slide, even if a library's slide-shape iterator omits them.
Do not export unused media files merely because they exist inside the PPTX archive.
Use displayable chart text without promising arbitrary underlying workbook extraction.

### Export completeness and warnings

Every slide requires a full-slide preview.
Every visible source drawable occurrence requires an isolated image, group image, contextual crop, or an explicit failure.
A group export does not erase child identity. Child exports remain available without becoming duplicate primary search results.
Objects wholly outside the slide or with no visible area receive an explicit not-visible outcome rather than a fabricated image.
Original-image availability is not required for generated graphics.
A cropped image containing several overlapping objects remains associated with its source occurrence and carries a context warning.
Use structured warning codes, affected element keys, and user-readable messages.
A job may succeed with warnings when every required rendition exists using an accepted method.
Missing required previews, unknown lost elements, or failed required renditions fail the extraction job.
A successful extraction does not imply that vision understanding, embeddings, or search indexing have completed.
Expose per-stage counts so a consumer can distinguish queued work, extraction, rendering, publication, and failure.

### Job API proposal

The extraction API prefix is `/v1`. Finalize schemas in the service API contract during EX1.

| Operation | Proposed endpoint | Result |
| --- | --- | --- |
| Submit deck bytes | POST /v1/decks/{deck_id}/extractions | Multipart bytes, source revision, and idempotency key. Return 202 with job ID, state, and status link |
| Read job | GET /v1/jobs/{job_id} | Return 200 with state, progress, warnings, error, and manifest link when complete |
| Retry failed job | POST /v1/jobs/{job_id}/retry | Return 202 with a new attempt and job ID. Retain source revision and reject deleted or superseded work |
| Read manifest | GET /v1/decks/{deck_id}/manifest | Return 200 for the published generation. Support a generation selector and bounded response size |
| Download bytes | GET /v1/assets/{asset_id} | Return authenticated bytes with media type, length, checksum validator, and safe attachment name |
| Remove extraction data | DELETE /v1/decks/{deck_id} | Persist fence before cancellation and return 202 while cleanup is pending |
| Read cleanup | GET /v1/decks/{deck_id}/deletion | Return cleanup status. Repeat deletion safely without recreating data |
| Health | GET /health | Report service and renderer readiness without source content, paths, or credentials |

Use 400 for malformed requests, 401 or 403 for authentication failures, 404 for unavailable resources, 409 for stale or conflicting work,
410 for attempts to submit against a deleted deck identity, 413 for exceeded request limits, 415 for unsupported file formats,
422 for invalid presentation content, 429 for a full bounded queue, and 503 for an unavailable renderer.
Once a job is accepted, report conversion failures through its status response with an error code, failed stage, retryability, and safe message.
Use a consistent error envelope and request ID. Do not return internal tracebacks or slide text.
Specify request limits, polling interval guidance, response pagination if required by measured manifest size, and retry behavior in the API contract.
Check authentication on manifests and all asset downloads. Keep download URLs relative or configured without leaking filesystem paths.

### Retry, reprocessing, and deletion

States are queued, processing, succeeded, failed, and cancelled.
Persist a request key and payload digest. Repeated identical submissions return the existing job.
Reject reuse of a request key for different bytes or parameters.
Stage each attempt in its own directory and publish only a complete generation.
In one metadata transaction, compare source revision, generation, and deletion fence before switching the active manifest.
Crash recovery marks abandoned work retryable or safely requeues it. Never publish half-written assets.
Keep a previous complete generation available during reprocessing and label it with its source revision.
Do not combine new text with old images. Remove superseded files after references are released.

On deletion the library first persists a tombstone and hides the deck from search and downloads.
It durably queues deletion for extraction and every implemented downstream consumer.
Extraction persists its own fence before cancelling jobs, removes public access immediately, then removes work copies and all derivatives.
A worker must check the fence before publication. A late worker cannot recreate metadata or active assets.
Downstream index owners must enforce the same deck and revision fence in their own commit transaction.
A pre-write HTTP check alone does not prevent a deletion race.
Keep minimal deletion fences without original filenames, slide text, or assets.
Retain cleanup progress and retry until every implemented service confirms removal. Report failed cleanup actionably.
Re-upload after deletion creates a new deck identity. Restoring backups must replay deletion fences and pending cleanup.

### Untrusted files and deployment

Validate the OOXML package type rather than trusting the filename.
Set compressed size, total uncompressed size, entry count, compression ratio, slide count, image pixel area, and conversion time limits.
Reject path traversal, unsafe XML expansion, encrypted files without a supported workflow, and legacy PPT.
Do not fetch linked images, linked workbooks, or other external resources.
Run conversion with restricted filesystem access, no network egress, and macros disabled. Hidden mode alone is not a security boundary.
Keep original bytes immutable and serve unsafe original formats as authenticated attachments with safe response headers.
Use PNG for inline graphic previews. Do not inject uploaded SVG or other active document content into the page.
Pin renderer and dependency versions after the proof. Record font availability and substitutions.
Verify PyUNO compatibility and installation separately on Windows and Linux.
No GPU or model endpoint is required by extraction itself.
The full indexing capacity and 8 GB VRAM requirements remain to be measured in the indexing feature.

## Appendix C. Alternatives rejected and risks

| Candidate | Decision and reason |
| --- | --- |
| python-pptx plus headless LibreOffice | Recommended candidate. Separate structured extraction and byte preservation from page and object rendering. Requires a source-to-renderer mapping proof |
| python-pptx alone | Suitable for structured extraction. The reviewed API does not establish a complete slide and SmartArt rendering solution |
| LibreOffice alone | Viable comparison in EX1. Select it only if source identity, native image bytes, and structure remain sufficient on the fixtures |
| A new pure-library renderer | Prefer in principle if it proves coverage. No reviewed Python library currently has proven coverage for this project's required fixtures. Do not build a renderer from scratch |
| Microsoft PowerPoint | Keep as a manual visual reference where available. Do not require it or build an automatic fallback in the initial Windows and Linux solution |
| Cropping every object from the slide | Accepted fallback, not the primary method. Overlap and backgrounds can reduce graphic reuse quality |

The recommendation is an architectural judgment supported by documented APIs, not a measured ranking.
Font substitution, unsupported SmartArt, effects, and grouped transforms can change appearance.
Close visual similarity permits differences, but not silent loss of content. Keep fixture images for human visual acceptance.
An isolated export preserves its own effects where the renderer supports them. It cannot promise the pixels of an occluded original slide region.
The first proof may invalidate LibreOffice object isolation or mapping. In that case use the approved labelled crop fallback where the slide is correct.
If slide rendering itself fails fidelity acceptance, revisit the renderer before production code proceeds.
Both operating systems have retained LibreOffice fixture renders and source-mapping reports. This does not establish native Windows confinement or SmartArt fidelity against PowerPoint. No representative performance or memory baseline has been measured.

Model the Domain shaped the separation of shared image bytes, source occurrences, and renderings.
Sequence Work into Verifiable Units shaped the EX1 dependency and the renderer proof before user-facing integration.
Prove It Works shaped the requirement for actual exported PNGs and cross-platform evidence.
Encode Lessons in Structure shaped the manifest coverage invariant and the plan format check.

## Appendix D. Links and reading list

- [Project constitution](../constitution.md).
- [Project instructions](../../AGENTS.md).
- [python-pptx overview](https://python-pptx.readthedocs.io/en/latest/). Documents platform-independent structured extraction and incomplete format coverage.
- [python-pptx image API](https://python-pptx.readthedocs.io/en/latest/api/image.html). Documents access to embedded image bytes and metadata.
- [LibreOffice drawing and presentation developer guide](https://wiki.documentfoundation.org/Documentation/DevGuide/Drawing_Documents_and_Presentation_Documents). Documents page and shape export.
- [LibreOffice XGraphicExportFilter](https://api.libreoffice.org/docs/idl/ref/interfacecom_1_1sun_1_1star_1_1drawing_1_1XGraphicExportFilter.html). Documents page, shape, and shape-collection sources.
- [LibreOffice graphics export parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/graphic_export_params.html). Documents PNG export options, image size, and translucency.
- Use current Context7 documentation before writing library-specific calls.
- Use pstack how and architect when implementing the module boundary. Use interrogate if renderer or lifecycle design remains contested.
