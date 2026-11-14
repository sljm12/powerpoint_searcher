# Web gateway API

The gateway serves the built frontend, creates shared-passphrase browser sessions, and proxies `/v1/...` to the configured library URL. It never reads library storage. The browser never receives the library bearer credential. Browser requests must use a configured allowed host and an origin equal to `GATEWAY_ORIGIN` or one listed in `GATEWAY_ALLOWED_ORIGINS`. In local mode, `http://127.0.0.1:5173` is also allowed for the Vite development server.

## Configuration

Set `LIBRARY_URL`, `LIBRARY_TOKEN` (at least 32 characters), `TEAM_PASSPHRASE_SALT`, and `TEAM_PASSPHRASE_HASH`. Generate the salted scrypt values with `uv run python scripts/make_passphrase_hash.py`; the passphrase itself is not stored. Set `FRONTEND_DIST` if the build is not at `../../frontend/dist` from the gateway working directory.

For local-only use, set `GATEWAY_LOCAL_MODE=true`, `GATEWAY_ALLOWED_HOSTS=127.0.0.1:8080`, and `GATEWAY_ORIGIN=http://127.0.0.1:8080`. The Vite development origin `http://127.0.0.1:5173` is allowed automatically. Keep the listener on loopback. For team deployment, set the exact public host in `GATEWAY_ALLOWED_HOSTS`, the HTTPS origin in `GATEWAY_ORIGIN`, and `GATEWAY_HTTPS_TERMINATED=true`; terminate HTTPS at a trusted reverse proxy and do not expose the gateway or library directly. This service trusts no forwarded identity headers. Restart invalidates all sessions. Use one worker because sessions and rate limits are in memory.

## Routes

| Route | Behavior |
| --- | --- |
| `POST /auth/login` | JSON `{ "passphrase": "..." }`; sets an HttpOnly, SameSite=Lax, eight-hour cookie. 400 malformed input, 401 wrong passphrase, 403 invalid origin, 429 throttled. |
| `GET /auth/session` | Returns `{ authenticated, csrf_token }`; requires a valid cookie. 401 expired or absent. |
| `POST /auth/logout` | Requires cookie, matching `X-CSRF-Token`, and allowed origin; returns 204 and clears the cookie. |
| `/v1/{path}` | Requires cookie; mutations also require `X-CSRF-Token`. Forwards method, query, body, status, safe content headers, request ID, and response bytes to the fixed library origin with server-side bearer auth. |

Credential and session responses use `Cache-Control: no-store`. Caller authorization, cookies, host, and forwarded identity headers are removed. Mutations are never retried. Upstream timeout returns 504; outage returns 502. Content responses stream and retain library status/error envelopes. Requests and content are not logged. Local mode uses a non-Secure cookie because it runs on loopback HTTP; team mode requires HTTPS and Secure cookies.
