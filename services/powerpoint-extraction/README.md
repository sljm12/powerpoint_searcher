# PowerPoint extraction service

This independently startable Python service validates `.pptx` uploads, extracts source text and picture bytes, renders slides and drawable objects with LibreOffice, and publishes a durable, versioned manifest. It owns its jobs, private work copies, metadata, and output assets. It does not perform embeddings or search indexing.

## Install and run

Use Python 3.12 or later and `uv`. Install LibreOffice Impress and its UNO bridge for the same operating system. Linux also needs bubblewrap, a systemd user manager with delegated cgroup v2 memory and process limits, and fonts. Use a dedicated NTFS directory on Windows or a private Linux filesystem directory. The repository is on exFAT and is not suitable for service data.

```powershell
uv sync --locked
$env:EX1_TOKEN = '<random secret of at least 32 bytes>'
$env:EX1_DATA_DIR = "$env:LOCALAPPDATA\PowerPointSearcher\extraction"
uv run python -m powerpoint_extraction
```

On Linux, including WSL, install LibreOffice Impress, `python3-uno`, bubblewrap, and the required fonts. The user systemd manager must have delegated cgroup v2 memory and process limits.

```sh
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/powerpoint-extraction-venv"
uv sync --locked
export EX1_TOKEN="$(openssl rand -hex 32)"
export EX1_DATA_DIR="$HOME/.local/share/powerpoint-extraction"
export EX1_BRIDGE=/usr/bin/python3
export EX1_OFFICE=/usr/bin/soffice
uv run python -m powerpoint_extraction
```

Keep both the uv environment and private service data on a Linux filesystem. The WSL checkout may be under `/mnt/d`, but its exFAT-backed files should not hold Python virtual environments or private service data.

The service binds to `127.0.0.1:8001` by default. `EX1_HOST` and `EX1_PORT` configure the listener. The renderer initializes during startup. `/health` reports whether the configured confined runtime is ready. Native Windows rendering currently fails closed because LibreOffice's UNO pipe cannot be reached inside the tested zero-capability AppContainer. Linux rendering requires the documented bubblewrap and cgroup setup. There is no unconfined fallback.

`EX1_DATA_DIR` must be an empty, dedicated directory. Windows startup protects it with a current-user-only ACL and rejects links. Linux requires owner-only permissions. One service process owns the directory through an exclusive lifetime lock. Run one server worker and one instance per directory.

## API and behavior

All content routes require `Authorization: Bearer $EX1_TOKEN`. The service accepts multipart uploads at `POST /v1/decks/{deck_id}/extractions`, then exposes job status, a validated source and rendition manifest, authenticated asset downloads, retry, and deletion. The complete contract, schemas, errors, and limits are in [API.md](API.md). Public OpenAPI routes are disabled; `create_app(...).openapi()` generates the contract for local checks.

Extraction is complete only when every required slide preview and drawable occurrence has a valid rendition or explicit supported outcome. Original embedded picture bytes remain separately downloadable. Failed conversions do not replace a previous complete generation. Deletion fences jobs and publication before removing content. Linux confinement currently covers LibreOffice conversion; Python package inspection still runs in the service process. See [LIMITATIONS.md](LIMITATIONS.md) for unresolved security, platform, and fidelity gates.

## Verify independently

```powershell
uv run --locked pytest -q
uv run mypy src
uv run ruff check src tests
```

Linux renderer and live HTTP evidence is retained under [evidence](evidence/README.md). The renderer proof uses trusted fixtures and demonstrates source-to-renderer coverage; it is not a general guarantee of PowerPoint pixel fidelity. The automated suite checks source validation, lifecycle behavior, API boundaries, and renderer adapter behavior. Linux-only confinement and real LibreOffice tests skip on Windows. Exact cross-platform commands and results are in [evidence/checks.md](evidence/checks.md).

The repeatable Linux capacity probe is documented in [probes/CAPACITY.md](probes/CAPACITY.md). It checks 20-slide coverage and 100 sequential deck identities through the HTTP API. Run it on a fresh, private Linux data directory before making deployment capacity claims.

## Dependencies

Python dependencies are pinned by `uv.lock`. LibreOffice and its Python UNO bridge are OS dependencies, not downloaded by `uv`. The Linux bridge must match the system LibreOffice installation. Renderer and archive limits are documented in `LIMITATIONS.md` and the API contract.
