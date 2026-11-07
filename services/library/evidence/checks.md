# Library service checks

Run on Windows with Python 3.12.11 on 2026-11-07. The target module's isolated offline environment could not be initialized because the sandbox blocked Python startup and its uv cache lacked the locked `mypy==1.20.2` wheel. The final source was checked using the already-installed isolated environment at `.audit/ex2-service/.venv`, with `PYTHONPATH` pointed at `services/library/src`.

| Command | Result |
| --- | --- |
| `python -m pytest services/library/tests -q` | 11 passed, 1 Starlette deprecation warning. |
| `python -m mypy services/library/src` | No issues in 7 source files. |
| `python -m ruff check services/library/src services/library/tests` | All checks passed. |

The exact interpreter was `.audit/ex2-service/.venv/Scripts/python.exe`. The equivalent target command, `uv run --locked --offline pytest -q`, could not resolve the locked mypy wheel because it was absent from the machine cache and network access is disabled.

The checks use a deterministic authenticated extraction HTTP contract. They verify upload-key replay, unchanged original checksums, crash recovery, same-revision retry after a lost response and after the automatic retry budget is exhausted, stale publication rejection, immediate download revocation, local deletion during an extraction outage, cleanup after restart, unknown-UUID deletion replay, asset integrity, bounded outbound deadlines, bounded outage retries, durable queue waiting, and safe metadata-validation errors.

No real LibreOffice, live frontend workflow, Linux workflow, screenshot, video, or capacity run was performed. These results do not establish EX2 live or performance acceptance. The browser-control approval timed out, so visual UI review was unavailable.

Current HTTPX 0.28.1 documentation was resolved and fetched through Context7 as `/encode/httpx/0.28.1`. It confirmed streamed multipart uploads and async response streaming with automatic connection cleanup.
