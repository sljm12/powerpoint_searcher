# Renderer gate evidence, 2026-11-02

This is a trusted-fixture proof, not authorization to process hostile presentations. No performance baseline or PowerPoint reference image was measured. All paths below are relative to this evidence folder.

| Run | Source SHA-256 | Source occurrences | Failed outcomes | Missing mapped objects/text | Renderer |
| --- | --- | --- | --- | --- | --- |
| windows | `23b59f0c692d8a058d869abb1fdfff70e3d2625969bcd858afce9bd91ae9748f` | 14 | 0 | 0 / 0 | LibreOffice 26.8.2.2 |
| linux | `23b59f0c692d8a058d869abb1fdfff70e3d2625969bcd858afce9bd91ae9748f` | 14 | 0 | 0 / 0 | LibreOffice 24.2.7.2 |
| windows-smartart | `a6a9011c6d7b542a91dfcc1e6ba4c2e7d468f027b8ceab9663882b0f77efdf08` | 1 | 0 | 0 / 0 | LibreOffice 26.8.2.2 |
| linux-smartart | `a6a9011c6d7b542a91dfcc1e6ba4c2e7d468f027b8ceab9663882b0f77efdf08` | 1 | 0 | 0 / 0 | LibreOffice 24.2.7.2 |

The live Linux service proof was repeated against the assembled checkout on 2026-11-05. [HTTP evidence](http-final2/responses.json) records successful ordinary upload, replacement, restart recovery and retry, genuine SmartArt extraction, invalid upload rejection, deletion during rendering, and verified downloads. The service shutdown left no active renderer scope. [Capacity evidence](capacity-20261105/README.md) records the 20-slide and 100-deck serial run, including exact source hashes and deletion storage measurements.

Both operating systems use the same fixture bytes. Windows generated the ordinary fixture; the final Linux run read `windows/representative.pptx`. Each directory retains `proof.json`, `uno.json`, page PNGs, isolated shape/group PNGs, forced contextual crop evidence, worker output and original image bytes. UNO-only inventory also records hidden master placeholders that are absent from the source inventory's visible drawable set.

The Windows proof exported all ordinary shapes including both group parents, their children, cropped alpha pictures, original image occurrences, chart/table and inherited master content. Original hashes remained unchanged. Linux independently exported the same source occurrences. Source metadata was subsequently enriched with table text blocks and inherited-field failure warnings. `refresh_inventory.py` asserts the original source checksum is unchanged, then regenerates the source inventory and mapping against the retained Windows render outputs. It does not regenerate or replace those Windows PNGs.

`alpha-pixels.json` retains actual unrotated picture pixel samples for Windows and Linux. Both samples preserve the source red ellipse alpha value of 190 within one level. Each proof also records per-rendition alpha extrema. Thus the alpha check establishes partial transparency, not merely an alpha channel filled with 255.

The lead visually inspected Windows and Linux ordinary page exports and Windows cropped-picture/group exports. Text, table cells, chart labels, nested groups, cropped images, connector and inherited master graphic were present. Automatic chart axes differed between LibreOffice versions, with a Windows upper bound of 8 and Linux upper bound of 10. Content remained present. The SmartArt chevrons and `a`, `b`, `c` survived import and isolated export on both platforms, but the white letters sit at the left chevron edge. Close fidelity to PowerPoint remains provisional without an original PowerPoint reference.

Commands were run from the module folder. Windows used CPython 3.12.11 and LibreOffice's Python UNO bridge 3.13.17. Linux used module CPython 3.12.3 and `/usr/bin/python3` UNO. The pinned Python dependencies are in `../uv.lock`.

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | Exit 0, 12 checks before the final inherited-field/UTF-16/table-cell additions. |
| `.venv/Scripts/python.exe -m ruff check src tests` | Exit 0. |
| `.venv/Scripts/python.exe -m mypy src/powerpoint_extraction/source.py src/powerpoint_extraction/proof.py src/powerpoint_extraction/fixtures.py` | Exit 0, three files. |
| `.venv/Scripts/python.exe -m powerpoint_extraction.proof --output evidence/windows` | Exit 0, 14 successful outcomes. |
| `.venv/Scripts/python.exe -m powerpoint_extraction.proof --output evidence/windows-smartart --fixture fixtures/smartart-chevron.pptx` | Exit 0, one successful outcome. |
| `/home/stephen/.cache/ex1-renderer-venv/bin/python -m pytest -q` | Exit 0, final 15 checks. |
| `/home/stephen/.cache/ex1-renderer-venv/bin/python -m ruff check src tests` | Exit 0. |
| `/home/stephen/.cache/ex1-renderer-venv/bin/python -m mypy src/powerpoint_extraction/source.py src/powerpoint_extraction/proof.py src/powerpoint_extraction/fixtures.py` | Exit 0, three files. |
| `/home/stephen/.cache/ex1-renderer-venv/bin/python -m powerpoint_extraction.proof --output evidence/linux --fixture evidence/windows/representative.pptx` | Exit 0, 14 successful outcomes. |
| `/home/stephen/.cache/ex1-renderer-venv/bin/python -m powerpoint_extraction.proof --output evidence/linux-smartart --fixture fixtures/smartart-chevron.pptx` | Exit 0, one successful outcome. |
| `/home/stephen/.cache/ex1-renderer-venv/bin/python evidence/refresh_inventory.py` | Exit 0, unchanged original hashes, source mappings and both pixel alpha assertions. |

Final unit checks cover source identities and original image bytes, three image occurrences sharing one asset, cropping/rotation, nested group hierarchy, inherited master content, hidden status, stable stamp copies, genuine SmartArt diagram parts, table cell row/column metadata, explicitly enabled inherited fields retained as unsupported failures, archive traversal/Windows drive names, DTDs including UTF-16, external links, active content, outcome coverage, alpha metadata, missing-slide failure and affine crop dimensions.

Enabled inherited dynamic placeholders remain unsupported for effective field resolution. They now produce explicit source records and fail the required slide rendition, so they cannot silently disappear. OS confinement is a separate integration gate. Lifecycle jobs, retries, deletion fencing, authentication, HTTP contracts and performance measurements are outside this renderer proof.
