# PowerPoint library frontend

The TypeScript frontend uploads PowerPoint decks to the library service. It shows extraction status, slide previews, graphic renditions, warnings, and authenticated downloads. It does not call the extraction service.

## Configure and run

Install Node.js 22.6 or later. Copy `.env.example` to `.env.local` and set `VITE_LIBRARY_URL` to the library service URL. The library must allow the frontend origin in `LIBRARY_ORIGINS` when the browser uses a different origin.

```powershell
npm ci
npm run dev
```

Open the local URL printed by Vite. Enter the library access token on the page. The frontend keeps the token in memory and sends it as a bearer token. It does not write the token to browser storage.

Build static files with:

```powershell
npm run build
```

The build writes `dist/`. In a deployed setup, serve those files and route `/v1` requests to the library service. Set `VITE_LIBRARY_URL` at build time only when the API uses a separate origin.

## Verify

```powershell
npm run check
npm test
npm run build
```

The TypeScript compiler checks strict types before Vite builds the page. `npm test` checks runtime parsing of deck and manifest responses. The live upload, download, warning, retry, and deletion workflows require the library and extraction services. Their commands and limits are documented in their own READMEs.
