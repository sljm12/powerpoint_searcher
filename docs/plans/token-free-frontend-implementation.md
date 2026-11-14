# Token-free frontend implementation plan

Status: Not started. Team access without existing sign-in is confirmed. Implements the proposed shared-passphrase design in the [feature plan](token-free-frontend.md) after review.

## Phase 1: settle and implement the server boundary

Input: the frontend currently calls the bearer-authenticated library directly.

- [ ] Build the proposed Python web gateway for team deployment, including shared-passphrase sign-in and browser sessions. Keep library and other backend listeners private. Document HTTPS termination and trusted forwarding explicitly.
- [ ] Define and check the session API before coding: `POST /auth/login` accepts the team passphrase and creates a fresh session cookie; `GET /auth/session` returns authenticated state and the session's CSRF token; `POST /auth/logout` invalidates the session. Success is 200/204 as documented; malformed requests return 400, failed authentication 401, failed origin/CSRF checks 403, and rate limiting 429. Credential responses use no-store. Define bounded request schemas, session expiry, and safe errors in the gateway contract.
- [ ] Implement password-hash configuration, bounded sign-in throttling, random opaque sessions, expiry, secure cookie attributes, CSRF validation, and sign-out. Restart and password rotation invalidate sessions. Use one gateway process and document this constraint.
- [ ] Document the gateway contract: `/v1/...` preserves library methods, paths, query strings, body bytes, status codes, error envelopes, request IDs, and download headers. Forward only to the configured library origin; reject arbitrary destinations and unrelated paths. The browser supplies no library credential.
- [ ] Specify missing configuration, unauthorized access, upstream timeout, and upstream outage errors. Set timeouts and upload/response resource limits that support existing library workflows. Stream binary transfers and preserve idempotency keys. Do not automatically retry mutations.
- [ ] Strip caller-supplied authorization and untrusted identity headers before injecting the server-held library credential. Keep secrets and content out of logs. Preserve no-store behavior; never cache protected content as public static files.
- [ ] Serve the frontend at the gateway origin. Protect content routes before reading upload bodies. Apply the chosen host/origin, session, and CSRF controls, including browser access to previews and downloads.
- [ ] If a new Python gateway is required, keep its code, configuration, `uv` dependency files, focused checks, README, and API contract together. It calls the library over REST and never reads library storage or imports library internals.
- [ ] Use current documentation through the project's required Context7 workflow before implementing gateway/framework-specific configuration. No dependency or version is selected by this plan.

Proof: verify an authorized browser request succeeds, an unauthorized request is rejected, direct library access still requires its token, and upstream failures produce actionable sanitized errors. Check uploads and downloads without exposing the injected credential.

Checkpoint: the gateway can be exercised independently before switching the frontend. Rollback removes the unused gateway and retains the current UI.

## Phase 2: switch the frontend as one complete slice

Input: a tested server boundary is available.

- [ ] `frontend/src/client.ts`: remove the token constructor argument, token validation, and bearer header. Use same-origin `/v1` requests while retaining runtime response validation and error handling.
- [ ] `frontend/src/main.ts`: automatically check the browser session on startup, display a labelled passphrase sign-in when needed, and initialize the library after sign-in. Load policy and decks before enabling the library. Add sign-out and accessible retry, prevent concurrent initialization and duplicate timers, and handle expired access by hiding protected content, closing galleries, releasing image URLs, stopping polling, and disabling mutation controls.
- [ ] `frontend/index.html` and `frontend/src/style.css`: replace token controls with accessible team-passphrase sign-in and sign-out; provide connection status and retry controls with keyboard access.
- [ ] Keep a session CSRF token in frontend memory and attach it to mutations. Update focused client behavior checks for session failures, CSRF handling, and absence of backend authorization headers.
- [ ] Update every `LibraryClient` caller, `frontend/tests/client.test.mjs`, and the search/gallery HTML fixtures. Fixtures must exercise automatic startup and retry without inserting synthetic credentials.
- [ ] Update `frontend/.env.example`, `frontend/src/vite-env.d.ts`, and development routing so development uses the same server boundary. Remove the direct browser-to-library URL setting if it has no remaining use. Do not add a browser-exposed token variable.
- [ ] Update `frontend/README.md`, root `README.md`, and library deployment guidance. Explain administrator configuration, independent startup, local and deployed routing, and the selected access mechanism. Update `services/library/API.md` only if its actual contract changes; bearer authentication is expected to remain intact.

Proof: run `npm run check`, `npm test`, and `npm run build` in `frontend/`. Run checks for the affected gateway module using its documented commands, with `uv` for Python. Existing library authentication checks must continue to pass.

Checkpoint: deploy gateway configuration and frontend together. Rollback restores the previous build and token-entry UI; do not restore an exposed endpoint. No data migration is needed.

## Phase 3: verify the running application

- [ ] Open a clean browser session, sign in with the team passphrase, then reload: policy and decks appear without token entry or repeat sign-in. Verify sign-out, expiry, restart invalidation, failed sign-in throttling, and password rotation. Inspect browser traffic and static output for backend credential disclosure using disposable test secrets.
- [ ] Exercise initial outage, accessible retry, access expiry or denial, wrong gateway credential, and library timeout. Confirm upload policy gating and a single refresh loop after retry.
- [ ] Use a synthetic deck to upload, observe indexing, search, open gallery and slide details, download preview/graphic/original, and delete. Compare original bytes and confirm deleted content disappears.
- [ ] Check rejection through the gateway for unauthorized API and binary routes, cross-site writes, and untrusted host/identity headers. For local mode, confirm listeners are unreachable from another machine; for team mode, verify signed-out and unauthorized-team access is denied.
- [ ] Record actual commands, deployment mode, results, and remaining limitations here. Do not record credentials or presentation content.

Completion requires the feature acceptance criteria on the running system. Builds and synthetic browser fixtures alone do not establish completion.

## Planning verification

The planning task inspects the document diff, local links, and consistency with the constitution. Implementation, dependency lookups, application tests, and runtime verification are deferred until implementation is requested. The shared-passphrase choice and proposed session lifetime remain reviewable plan recommendations.
