# Frontend checks

Run on Windows with Node.js 22.17.1 on 2026-11-07 from `frontend/`.

| Command | Result |
| --- | --- |
| `npm run check` | Passed. |
| `npm test` | 7 passed. Node emitted its documented experimental type-stripping warning. |
| `npm run build` | Passed with Vite 7.3.7. |

The tests exercise runtime parsing of deck and manifest responses, malformed checksums, asset URL confinement to the expected deck generation, retained-generation validation during reprocessing, and upload-key replay after network loss or an uncertain 502 response. No live browser workflow, screenshot, video, or keyboard-only interaction run was performed. Browser-control app approval timed out.

## Text-search continuation check

On 2026-11-14, `npm run check`, `npm test` (9 passed), and `npm run build` passed. A fresh browser inventory found no browser automation surface because the browser access host was unavailable. No live page, responsive layout, keyboard flow, or screenshot was verified.
