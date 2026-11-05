# Linux capacity probe

`capacity.py` exercises the running service through authenticated HTTP. Its fixture has 20 slides, one named text shape and one repeated-byte embedded picture on every slide, plus a named table, named chart, and nested group on slide one. The probe requires each source shape in the manifest, explicitly checks table and chart kind and text, checks the group parent relationship, checks 20 distinct picture occurrences sharing one original asset, and downloads and verifies every published asset.

Run the fixture self-check first. Use a fresh private data directory on Linux ext4 and a new evidence path. Do not put the service token on the command line or in a checked-in file.

```sh
uv sync --locked
python probes/test_capacity.py
sha256sum -c probes/source-sha256.txt
export EX1_TOKEN="$(openssl rand -hex 32)"
export EX1_DATA_DIR=/home/<user>/.local/share/ex1-capacity-run
export EX1_CAPACITY_OUTPUT=/tmp/ex1-capacity-run
bash probes/run-capacity.sh
```

If the uv environment is outside the checkout, also set `UV_PROJECT_ENVIRONMENT` to its absolute Linux path. The service binds to loopback. The runner starts it, waits for renderer readiness, runs five serial 20-slide uploads, then 100 serial deck identities, downloads and validates every asset, deletes the decks through HTTP, and checks that output storage shrinks and does not regrow after two seconds. It stops the service on exit. A failed probe preserves its evidence and data directory for inspection.

The report records all job outcomes, request count, wall times for complete upload-to-download jobs, five-run median and range, logical data-directory size before and after deletion, 0.5-second service descendant RSS samples, renderer cgroup memory samples, machine load, fixture/source checksums, and verified work counts. Those measurements describe this run only. They do not compare against trunk, the LibreOffice-only path, or another system, and they do not establish cold-cache latency, a production SLO, indexing capacity, or a latency guarantee. Renderer memory is a sampled cgroup value and includes charged cache; it is not an exact process RSS peak. The service's renderer consumed about one CPU core during an earlier two-slide live conversion; this suggests conversion CPU is material, but it is not a profile of the 20-slide workload. Do not claim a capacity bottleneck or optimize from these measurements alone.

The first five runs are serial and use the same fixture bytes and a newly created LibreOffice profile per extraction. The OS page cache is not flushed. The subsequent 100 decks are processed sequentially by the single service worker. The source code SHA-256 snapshot in `source-sha256.txt` must match `src/powerpoint_extraction/` before interpreting the results. One completed run is retained in `../evidence/capacity-20261105/`; its summary has 105 successful jobs and zero failures. The repeat-run median was 4.381 seconds with a 4.220 to 4.792 second range. This is observational evidence from one WSL2 host, not a production latency claim. `runner-sha256.txt` identifies the service launcher used for that run; the summary contains the capacity-probe and source-snapshot hashes.
