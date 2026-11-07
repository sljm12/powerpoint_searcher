# Library service checks

Run on Windows with Python 3.12.11 on 2026-11-07 from `services/library/`.

| Command | Result |
| --- | --- |
| `uv run --locked pytest -q` | 22 passed, 1 Starlette deprecation warning. |
| `uv run --locked mypy src` | No issues in 8 source files. |
| `uv run --locked ruff check src tests scripts` | All checks passed. |
| `uv run --locked python scripts/check_database_workflow.py --self-check` | Driver self-check passed. |

The tests include schema-v1 backup and upgrade, original hash preservation, pending deletion preservation, missing-database refusal, marker mismatch refusal, incomplete-marker refusal before cleanup, recovery from an interrupted upgrade using the documented backup procedure, refusal of invalid legacy records and a mismatched schema before cleanup, persisted deck ordering, due-time ties, metadata transaction rollback, paths with spaces, and owner-lock release after failed startup. Existing authenticated API and deterministic extraction tests also pass.

No live extraction workflow, browser gallery check, screenshot, Linux run, or capacity measurement was performed. These results do not establish LIBDB-1 live or performance acceptance.

Current HTTPX 0.28.1 documentation was resolved and fetched through Context7 as `/encode/httpx/0.28.1`. It confirmed streamed multipart uploads and async response streaming with automatic connection cleanup.
