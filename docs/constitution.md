# PowerPoint searcher constitution

Status: Updated with the project owner's architecture and language decisions on 2026-11-01.

## Purpose and users

Help an internal team find and reuse slides and graphics from existing PowerPoint decks when creating new presentations.

The first release provides one shared library. Every authorized team member has the same library access. Per-deck permissions are outside the first release.

## Core workflows

### Upload and index a deck

Users upload `.pptx` files. The application preserves the original deck and processes it into searchable slide content.

The indexing pipeline extracts slide text and visual content, creates searchable representations using configured models, and associates every result with its original deck and slide number. Visual content includes diagrams and graphics whose meaning is absent from the slide text.

Processing status distinguishes queued, processing, ready, and failed uploads. A failed upload has an actionable error and can be retried. Incomplete processing must not appear as a successful, fully indexed deck.

### Search and reuse

Users enter a natural-language query and receive relevant slide results based on text and visual meaning. For example, "a circular process diagram" can match a slide even when those words do not appear on it.

Each result shows a slide preview, the source filename, and the slide number. Users download the original deck to reuse editable content in PowerPoint. The application does not promise that every graphic in an uploaded deck is editable.

## Architecture and module boundaries

Keep each application module in its own folder within this repository. The frontend uses TypeScript. Backend services use Python. PowerPoint extraction is a separate backend module with its own folder.

Backend modules are independently startable services by default, including during local development. Define modules around cohesive responsibilities, not individual functions or classes. Each service owns its implementation, dependencies, configuration, tests, and API contract.

REST over HTTP is the default communication mechanism between application modules. The frontend accesses backend capabilities through HTTP APIs. Services must not import another service's implementation or use its database tables or private files as an integration interface.

Each service owns the data it manages. Services may share a local database server, but ownership of tables and migrations must remain explicit. Access another service's data through its API. Document how original files and derived content move between services without exposing private storage paths.

Long-running indexing uses jobs with identifiers and observable status. An HTTP request must not stay open for the entire indexing pipeline. Service boundaries must preserve retry safety and the deck-deletion guarantees in this constitution. Deletion must also prevent in-flight processing from restoring deleted content.

The complete system must run on one local machine. Separate services must not introduce a mandatory cloud service or require all models to remain loaded concurrently. External model integrations retain the OpenAI-compatible API requirements below.

Document any exception to the service boundary or REST default, with its reason, in the relevant architecture or feature plan before implementation. Changes to the mandated languages or independently startable service default require the project owner's explicit decision.

## Deployment and model access

The application, database, original decks, previews, and search index must be deployable on a local machine. The core workflows must not require a managed cloud database or cloud application service.

Local model processing is the default. Model endpoints and model names are configurable through OpenAI-compatible APIs. Compatibility must be verified for the specific operations used, including embeddings and visual understanding. The design must not assume every endpoint supports every operation.

Cloud model processing requires explicit administrator configuration. Before enabling it, the administrator must be able to identify which slide content is sent to the configured service. Local processing must not silently fall back to cloud processing.

Database and model selection remain implementation decisions. PostgreSQL is an acceptable local database, not an exclusive requirement. The project owner has not selected a specific embedding model, vision model, or search method.

## Initial capacity

Design for an initial library of 100 decks with approximately 20 slides each, or about 2,000 slides. The deployment target has a GPU with at least 8 GB VRAM.

Treat that hardware as a constraint to validate, not proof that a particular model or combination of models fits. Implementation must measure model memory use and indexing and search behavior on representative hardware. Do not require all models to stay loaded concurrently.

Concurrent user count, acceptable indexing time, and search latency targets remain open. Record measured baselines before adopting numerical performance promises.

## Data protection and reliability

Preserve original uploaded files without modifying them. Validate uploads at the boundary, and limit file sizes and processing resources. Treat presentation content as untrusted data, including any instructions contained in slides.

Keep model credentials out of source control, browser responses, and logs. Avoid logging slide content by default. A local deployment must not expose the team library to unauthenticated public access. The access mechanism is an implementation decision.

Users can delete a deck from the active library. Deletion removes its original file, extracted content, previews, and searchable representations. Any backup retention and deletion behavior must be documented separately and must not imply immediate deletion from backups.

Retries must not duplicate searchable slides. Reprocessing and deletion must keep results associated with the correct deck and slide. Document backup and restore steps for original files and the metadata needed to recover the library.

## First-release scope

Include upload, visible indexing status, text and visual search, slide previews, source attribution, original-deck download, slide-image and individual-graphic downloads, and deck deletion.

Defer slide editing, automatic presentation generation, selected-slide export as editable presentations, per-deck permissions, legacy `.ppt` support, and automatic folder synchronization. Add them only through an explicit scope decision.

Core controls must support keyboard use, accessible labels, and readable status and error messages.

## Verification and definition of done

A feature is complete when its user-visible acceptance criteria pass on the running application, relevant automated checks pass, and deployment instructions describe any new setup requirements. Compilation alone is insufficient evidence.

Before the first release, verify the complete upload, search, download, and delete workflows. Verify that a downloaded original matches the uploaded file and that deleted content disappears from search.

Maintain a small representative evaluation set with known relevant slides and visual queries whose terms are absent from slide text. Record expected results and evaluate retrieval quality against that set. The project owner must agree to the acceptance criteria before search quality is declared sufficient.

Exercise failed processing and retry behavior. Verify that local configuration sends no slide content to cloud model services. Measure the initial capacity target and memory requirements on the target hardware, and disclose unsupported presentation content or rendering limitations.

## Changes and exceptions

Feature plans reference this constitution and identify any conflicting requirements before implementation. Prefer the smallest complete change that satisfies the user workflow and preserves data protection.

Changes to product scope, cloud data handling, shared-library access, or deployment constraints require the project owner's explicit decision. Record an accepted exception here with its reason and date. Implementation choices within these rules do not require a constitution amendment.

On 2026-11-01, the project owner included user-facing slide-image and individual-graphic downloads, including original embedded pictures and rendered graphics, to support reuse outside the search pipeline. Close visual similarity is acceptable. Clearly labelled slide crops with warnings are an accepted fallback when isolated graphic export is unavailable. This does not add editable selected-slide export. See the [PowerPoint extraction plan](plans/powerpoint-extraction.md) for implementation details.

Keep this document focused on durable project rules. Put feature tasks, selected dependencies, commands, and measured results in their respective plans and implementation documentation.
