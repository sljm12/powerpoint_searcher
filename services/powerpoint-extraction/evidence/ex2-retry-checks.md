# EX2 extraction retry contract checks

Run on Windows with Python 3.12.11 on 2026-11-07 from `services/powerpoint-extraction/`.

| Command | Result |
| --- | --- |
| `uv run --locked --offline pytest tests/test_lifecycle.py -q` | 9 passed, 1 Starlette deprecation warning. |
| `uv run --locked mypy src` | No issues in 16 source files. |
| `uv run --locked ruff check src tests` | All checks passed. |

The lifecycle test covers valid retry-key replay, invalid key rejection, unkeyed retry conflict, and key reuse conflict. These checks do not establish the live cross-service workflow or platform acceptance.
