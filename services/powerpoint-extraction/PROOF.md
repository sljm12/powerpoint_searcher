# EX1 implementation and verification record

The service now includes bounded `.pptx` inspection, a real LibreOffice renderer adapter, validated manifests and assets, an authenticated HTTP API, durable jobs, retry/recovery, generation replacement, and deletion fencing. This record names the retained evidence and its limits. It does not claim unconditional EX1 closure.

## Renderer coverage

The generated representative fixture contains ordinary text, a table, a chart with labels, three occurrences of the same alpha PNG bytes, a cropped and rotated picture, nested rotated and scaled groups, a connector, a non-placeholder master graphic, and a hidden slide. The separate genuine SmartArt fixture and license provenance are in `fixtures/`.

| Run | Source SHA-256 | Occurrences | Failed outcomes | Missing objects or text | LibreOffice |
| --- | --- | ---: | ---: | ---: | --- |
| Windows ordinary | `23b59f0c692d8a058d869abb1fdfff70e3d2625969bcd858afce9bd91ae9748f` | 14 | 0 | 0 / 0 | 26.8.2.2 |
| Linux ordinary | `23b59f0c692d8a058d869abb1fdfff70e3d2625969bcd858afce9bd91ae9748f` | 14 | 0 | 0 / 0 | 24.2.7.2 |
| Windows SmartArt | `a6a9011c6d7b542a91dfcc1e6ba4c2e7d468f027b8ceab9663882b0f77efdf08` | 1 | 0 | 0 / 0 | 26.8.2.2 |
| Linux SmartArt | `a6a9011c6d7b542a91dfcc1e6ba4c2e7d468f027b8ceab9663882b0f77efdf08` | 1 | 0 | 0 / 0 | 24.2.7.2 |

The same ordinary fixture bytes were used on both systems. Evidence retains source and LibreOffice inventories, page and isolated shape/group PNGs, forced contextual crop results, original image bytes, source hashes, and alpha samples. The unrotated source picture's partial alpha value of 190 was preserved on both systems. Visual inspection found chart axis bounds differ by LibreOffice version, while chart values and labels remain present. SmartArt source mapping and exports pass, but the white labels sit at the left chevron edges. PowerPoint visual parity remains provisional.

Native Windows conversion is not production-ready. The tested zero-capability AppContainer blocks filesystem and network access as intended, but LibreOffice cannot connect through its `OSL_PIPE_` UNO pipe. Native readiness therefore fails closed. Linux conversion is confined, but source validation and inventory generation still run in the service process. The full upload pipeline is not sandboxed. See `LIMITATIONS.md`.

## Live HTTP coverage

`evidence/http-final2/` retains a fresh real Linux service run and machine-readable responses. The ordinary two-slide deck completed with 17 verified downloadable assets. The probe downloaded each asset and checked byte length, SHA-256, and ETag. It also completed revision replacement, restart recovery followed by retry, a genuine SmartArt upload, invalid presentation rejection, and deletion during a processing job. Server logs and manifests are retained beside the response summary. After the restart flow, no EX1 renderer scope or LibreOffice process remained.

The preliminary capacity run was stopped when its verifier failed to independently assert table and chart source coverage. The corrected verifier adds those assertions, group-parent checks, repeated-picture identity checks, and full asset downloads. Its final result is retained in `evidence/capacity-20261105/README.md` and `summary.json`. Five serial runs of the 20-slide fixture and 100 sequential unique deck identities completed with zero failed jobs. The report gives the measured wall-time range and storage/memory sizes. These are single-host observations; no trunk comparison, cold-cache run, production limit, or 20-slide profiling result is claimed.

## Checks

Exact commands and results for Windows 11 and Ubuntu 24.04 WSL2 are in `evidence/checks.md`. Windows pytest completed with 28 passed and 6 Linux-only skips. Linux pytest completed with 32 passed and 2 Windows-only skips. Strict mypy and Ruff passed on both platforms. The confinement regression kills the service parent during live conversion and verifies the detached renderer scope is stopped. The live HTTP run confirms restart/retry and deletion also leave no converter process.

## Explicit limits

Enabled inherited footer, date, and slide-number placeholders are recorded but unsupported for effective value resolution. Their required slide renditions fail instead of silently omitting them. Polling limits bound output growth operationally but do not provide an exact filesystem quota. SmartArt fidelity lacks a trusted PowerPoint reference. Native Windows rendering is unavailable. Representative capacity results are not yet measured. See `LIMITATIONS.md` for closure evidence.
