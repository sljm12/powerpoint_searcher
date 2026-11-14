# Open the library without entering a token

Status: Proposed plan. Owner confirmed team access with no existing sign-in system. No implementation is authorized by this planning task.

## Outcome

Users open the application and reach the shared library without finding, copying, or entering `LIBRARY_TOKEN`. The proposed replacement is a shared team passphrase that creates a browser session. Reloading the page does not require signing in again while that session remains valid. The administrator configures the library token on the server once.

The frontend automatically loads library policy and decks. It shows loading, ready, and failed connection states with an accessible retry action. Upload stays unavailable until the notes policy has loaded. Existing search, gallery, upload, download, retry, and deletion behavior remains available.

## Project rules and existing behavior

This plan follows the [constitution](../constitution.md), especially shared access, HTTP service boundaries, configurable credentials, and protection against unauthenticated public access. No constitutional amendment is proposed. Simply removing library authentication would conflict with its data protection rule.

The [preliminary design](../design_doc.md), section 11, proposes identity verification and tenant permissions. Identity verification is relevant to network access, but tenant and per-deck permission work exceeds this first-release shared-library feature. The constitution takes precedence.

Today `frontend/src/main.ts` waits for token submission. `LibraryClient` validates the token and adds it to every request. Library middleware requires the bearer token on every route except `/health`. Browser tests and deployment instructions assume this connection flow. The repository has no configured browser session or identity provider integration.

## Recommended boundary

Use a same-origin server boundary: the browser requests `/v1/...`; a trusted gateway authorizes browser access, adds the configured library bearer token, and calls the library over HTTP. The gateway also serves the built frontend. The token never appears in browser JavaScript, HTML, storage, responses, URLs, or logs. Library authentication remains mandatory for direct API clients.

Prefer an existing deployment gateway when one is available. If a custom gateway is necessary, it is a cohesive Python service under `services/web-gateway/`, independently startable with its own README, dependency files managed by `uv`, and API contract. Do not implement production credential injection only in the Vite development server.

Compare the alternatives:

| Option | Assessment |
| --- | --- |
| Put the token in `VITE_*`, static configuration, or browser storage | Reject: distributes the shared credential and does not provide an access boundary. |
| Disable bearer authentication on the library | Reject: exposes content unless a replacement boundary is enforced. |
| Server gateway with protected browser access | Recommend: removes token handling from the frontend and preserves existing library contracts. |

## Access decision and proposed sign-in

- **This computer only:** an explicitly local deployment may use OS access as its trust boundary. Bind the gateway and backend listeners to loopback. Validate allowed host and origin and reject cross-site requests, including writes, to prevent another website using the local gateway. Do not expose this mode through a tunnel or network listener. This mode does not isolate different users of the same computer.
- **Team access with existing sign-in:** reuse the organization's authenticated reverse proxy or identity provider. Authorize team membership before forwarding any content request. Use HTTPS, protected browser sessions, and CSRF protection for mutations. Existing SSO may make sign-in automatic; users never enter the library token.
- **Team access without existing sign-in:** select an access mechanism before implementation. The gateway must refuse network operation without protection. Automatically handing every visitor a session is not authentication.

The owner selected **team access without an existing sign-in system**. Recommend a shared team passphrase for this first-release shared library. This is a proposed implementation choice for review, not an owner request for individual accounts or an identity-provider deployment. It removes library-token handling, but does not promise password-free first access.

The Python web gateway owns sign-in, browser sessions, and static frontend hosting. Configure a password hash and the library credential server-side. Never use `LIBRARY_TOKEN` as the team passphrase. Verify passwords with an established password-hashing implementation selected during implementation; store no plaintext passphrase. Rate-limit failed sign-in attempts and return generic errors without credentials.

Keep opaque, cryptographically random sessions in gateway memory for one process, with a proposed absolute eight-hour lifetime. Cookies are HttpOnly, Secure, SameSite=Lax, scoped to the application, and sent only over HTTPS for team deployment. Restart and passphrase rotation invalidate all sessions; sign-out removes the current session. Bound the session store and sign-in limiter. A single process matches the initial local-machine deployment; multiple gateway workers require a shared session design before adoption.

Use a session-bound CSRF token plus exact origin checks on mutations, including sign-in/sign-out protection. The browser may receive its own CSRF token; it never receives a backend service token. Check sessions on every API request, including binary content. Sessions do not extend access after expiry or logout. Pending downloads already in progress need no new revocation promise.

Shared access cannot identify or revoke one team member independently. Change the passphrase and invalidate all sessions when access must be removed. Adopt individual accounts or SSO when that limitation becomes unacceptable. No per-deck permissions are introduced.

## Acceptance criteria

1. Signing in with the team passphrase loads policy and decks without a library-token field or token prompt; reloading reuses the valid session.
2. All content and mutations pass through the chosen access boundary. Direct library requests without a valid bearer token still return 401.
3. Browser requests and built assets contain no library, extraction, search, or model credentials.
4. Existing workflows work through the gateway, including binary assets, original downloads, uploads, job polling, and deletion.
5. A failed initial request offers retry without creating duplicate refresh timers. Notes policy is visible before uploads become usable.
6. Unauthorized access and cross-site mutation attempts are denied. Network deployment without the selected access protection fails closed.

Implementation steps and verification are in [the implementation plan](token-free-frontend-implementation.md). No runtime behavior has changed or been verified by this planning task.
