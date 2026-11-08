# Library service checks

Run on Windows with Python 3.12.11 on 2026-11-08 from `services/library/`.

| Command | Result |
| --- | --- |
| `uv run --locked pytest -q` | 22 passed, 1 Starlette deprecation warning. |
| `uv run --locked mypy src` | No issues in 8 source files. |
| `uv run --locked ruff check src tests scripts` | All checks passed. |
| `uv run --locked python scripts/check_database_workflow.py --self-check` | Driver self-check passed. |

The tests include schema-v1 backup and upgrade, original hash preservation, pending deletion preservation, missing-database refusal, marker mismatch refusal, incomplete-marker refusal before cleanup, recovery from an interrupted upgrade using the documented backup procedure, refusal of invalid legacy records and a mismatched schema before cleanup, persisted deck ordering, due-time ties, metadata transaction rollback, paths with spaces, and owner-lock release after failed startup. Existing authenticated API and deterministic extraction tests also pass.

No live extraction workflow, browser gallery check, screenshot, Linux run, or capacity measurement was performed. These results do not establish LIBDB-1 live or performance acceptance.

Current HTTPX 0.28.1 documentation was resolved and fetched through Context7 as `/encode/httpx/0.28.1`. It confirmed streamed multipart uploads and async response streaming with automatic connection cleanup.

## LIBDB-2 implementation checks

Run on Windows with Python 3.12 and PostgreSQL 18.6 on 2026-11-08. The PostgreSQL fixture reads `LIBRARY_TEST_POSTGRES_DSN`, creates a uniquely named database for each test, and drops it during teardown. The configured database from `set_db.bat` was used only as a connection source; each test created and removed a separate database.

| Installation | Command | Result |
| --- | --- | --- |
| SQLite only | `uv sync --locked` | Passed. Psycopg and its binary package were absent. |
| SQLite only | `uv run --locked pytest -q` | 25 passed, 4 PostgreSQL-only cases skipped. |
| SQLite only | `uv run --locked mypy src` | No issues in 9 source files. |
| SQLite only | `uv run --locked ruff check src tests scripts` | All checks passed. |
| SQLite only | `uv run --locked python scripts/check_database_workflow.py --self-check` | Driver self-check passed. |
| PostgreSQL extra | `uv sync --locked --extra postgres` | Passed. Psycopg 3.3.8 and psycopg-binary 3.3.8 installed from the lock. |
| PostgreSQL extra | `uv run --locked --extra postgres pytest -q` | 29 passed, 1 Starlette deprecation warning. No skips. |
| PostgreSQL extra | `uv run --locked mypy src` | No issues in 9 source files. |
| PostgreSQL extra | `uv run --locked ruff check src tests scripts` | All checks passed. |
| PostgreSQL extra | `uv run --locked --extra postgres python scripts/check_database_workflow.py --self-check` | Driver self-check passed. |
| PostgreSQL extra | `uv lock --check` | Lockfile is current. |
| PostgreSQL extra | `git diff --check` | Passed; Git reported only the repository's LF/CRLF working-copy notices. |

The PostgreSQL tests cover configuration, safe 503 and health behavior, the database owner lock, root binding, populated-target refusal, both transfer directions, original hashes, unknown-UUID deletion fences, archive retention, stale-source refusal, resume after target activation, and shared upload/deletion/restart persistence on both backends. The workflow driver now has opt-in offline-transfer and storage-unavailable probes; only its self-check was run. These tests do not run the extraction worker against PostgreSQL.

No outage timing, stalled-query responsiveness, 100-deck capacity probe, full extraction workflow, frontend gallery check, Linux run, screenshot, or operator review was performed. The 5 second connection and statement controls, 1 second lock control, keepalives, and TCP user timeout were exercised on successful PostgreSQL connections. Broken-network timing remains unverified.
