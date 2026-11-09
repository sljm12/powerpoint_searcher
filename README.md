# PowerPoint Searcher

PowerPoint Searcher is a local application for a team to upload `.pptx` decks, index their slide content, and find slides to reuse. Search results include slide previews and source details; the original deck remains available for download.

The repository contains a TypeScript frontend and independently startable Python services. The library owns uploaded decks and coordinates extraction. The extraction service validates and renders presentations. The search service indexes extracted slide text and speaker notes using PostgreSQL full-text search and vector search. This milestone provides text search; visual-meaning search remains future work. See the [project constitution](docs/constitution.md) and [text search design](docs/text-search-design.md) for scope and decisions.

## Run locally

Use Node.js 22.6 or later, Python 3.12 or later, `uv`, and LibreOffice Impress with its UNO bridge. The extraction renderer is verified in WSL2/Linux; native Windows rendering is unavailable. Start each service in its own terminal and follow its README for required private data directories and configuration.

1. Start the extraction service by following [services/powerpoint-extraction/README.md](services/powerpoint-extraction/README.md). It listens on `http://127.0.0.1:8001` by default.
2. Start the library service by following [services/library/README.md](services/library/README.md). It listens on `http://127.0.0.1:8000` by default and needs an `EX1_TOKEN` matching the extraction service token.
3. Start the search service by following [services/search/README.md](services/search/README.md). It requires its configured PostgreSQL/pgvector database and embedding endpoint. The library and search service must be configured to communicate with each other as documented in their service READMEs.
4. In `frontend/`, copy `.env.example` to `.env.local`, set `VITE_LIBRARY_URL=http://127.0.0.1:8000`, then run:

   ```powershell
   npm ci
   npm run dev
   ```

Open the local Vite URL. Provide the library access token in the page. Generate and store service tokens and model credentials in protected local configuration; do not commit them. Speaker notes are indexed by default, and the active policy is shown before upload.

## Module documentation

Each module documents its own configuration, API, dependencies, and independent verification commands:

- [Frontend](frontend/README.md)
- [PowerPoint extraction](services/powerpoint-extraction/README.md)
- [Library](services/library/README.md)
- [Search](services/search/README.md)

## Project documents

- [Constitution](docs/constitution.md): durable product, architecture, and data-protection requirements.
- [Preliminary design](docs/design_doc.md): provisional architecture guidance.
- [Plans](docs/plans/): implementation details and feature-specific decisions.
