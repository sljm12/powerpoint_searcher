# Web gateway

The gateway owns shared-passphrase sign-in, one-process browser sessions, CSRF checks, static frontend hosting, and the same-origin `/v1` proxy. It calls the library over REST and does not access its database or files. See [API.md](API.md) for routes and error behavior.

## Configure and run locally

Install Python 3.12+ and `uv`. Generate a passphrase hash and salt without writing the passphrase to disk:

```powershell
uv sync
uv run python scripts/make_passphrase_hash.py
```

Set the displayed hash and salt, plus `LIBRARY_TOKEN` and `LIBRARY_URL`, in the protected environment. For loopback-only local operation also set:

```powershell
$env:GATEWAY_LOCAL_MODE = 'true'
$env:GATEWAY_ALLOWED_HOSTS = '127.0.0.1:8080'
$env:GATEWAY_ORIGIN = 'http://127.0.0.1:8080'
uv run python -m gateway
```

Build the frontend first with `npm run build` in `frontend/`. The gateway defaults to `frontend/dist` relative to this service. `GATEWAY_HOST` and `GATEWAY_PORT` configure its listener; keep local mode bound to loopback.

## Team deployment

Use one gateway process and terminate HTTPS at the trusted reverse proxy. Set `GATEWAY_HTTPS_TERMINATED=true`, `GATEWAY_ALLOWED_HOSTS` to the exact public host and port, and `GATEWAY_ORIGIN` to its HTTPS origin. Set `GATEWAY_HOST` to a private interface reachable only by the proxy. Keep the library and gateway unreachable from public clients. No forwarded identity headers are trusted. Restart or passphrase rotation invalidates sessions. Shared access cannot revoke one team member independently; change the passphrase to invalidate all sessions.

Set `LIBRARY_URL`, `LIBRARY_TOKEN`, `TEAM_PASSPHRASE_HASH`, and `TEAM_PASSPHRASE_SALT` from the deployment secret store. The gateway has no cloud requirement. `uv sync` installs its isolated dependencies; `uv run python -m gateway` starts it independently.

## Verify

```powershell
uv run ruff check src tests scripts
uv run mypy src
uv run pytest
```

The frontend's documented npm checks run from `frontend/`. Full workflow verification also needs the library and extraction services running.
