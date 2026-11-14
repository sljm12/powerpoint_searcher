# EX1 checks

Commands were run from `services/powerpoint-extraction/` unless shown otherwise. All listed test, mypy, Ruff, and fixture-check commands exited 0.

| Platform | Command | Result |
| --- | --- | --- |
| Windows 11 | `uv run --locked pytest -q` | 28 passed, 6 Linux-only skips |
| Windows 11 | `uv run mypy src` | No issues in 16 source files |
| Windows 11 | `uv run ruff check src tests` | All checks passed |
| Ubuntu 24.04 in WSL2 | `bash .audit/ex1-linux-check.sh` from repository root | 32 passed, 2 Windows-only skips |
| Ubuntu 24.04 in WSL2 | `uv run --locked mypy src` | No issues in 16 source files |
| Ubuntu 24.04 in WSL2 | `uv run --locked ruff check src tests` | All checks passed |
| Ubuntu 24.04 in WSL2 | `uv run --locked python probes/test_capacity.py` | Fixture/source coverage self-check passed |
| Ubuntu 24.04 in WSL2 | `bash .audit/ex1-capacity-run-final.sh` from repository root | Exit 0; 105 verified jobs, zero failures; details in `capacity-20261105/summary.json` |

The Windows FastAPI test client reports a Starlette deprecation warning because its current integration uses `httpx`; it did not fail the checks. Linux pytest reports the same warning.

Live HTTP outcomes are captured in `http-final2/responses.json`. That file reports `passed: true`; `http-final2/server.log` ends with a completed application shutdown. The HTTP probe checks asset lengths, SHA-256 values, and ETags for every returned asset.

## Text-search continuation check

On 2026-11-14, these commands ran from `services/powerpoint-extraction/`:

| Command | Result |
| --- | --- |
| `uv run --locked pytest -q` | 37 passed, 6 skipped, 1 Starlette deprecation warning. |
| `uv run --locked mypy src` | No issues in 16 source files. |
| `uv run --locked ruff check src tests` | All checks passed. |

The skipped tests were not run against a live extraction workflow. No upload or extraction job was submitted to the running service.
