#!/usr/bin/env bash
set -euo pipefail

: "${EX1_TOKEN:?Set EX1_TOKEN to a random secret of at least 32 bytes}"
: "${EX1_DATA_DIR:?Set EX1_DATA_DIR to a new private Linux filesystem directory}"
: "${EX1_CAPACITY_OUTPUT:?Set EX1_CAPACITY_OUTPUT to a new evidence directory}"
test ! -e "$EX1_DATA_DIR"
test ! -e "$EX1_CAPACITY_OUTPUT"

ex1_port="${EX1_PORT:-8018}"
service_python="${UV_PROJECT_ENVIRONMENT:-.venv}/bin/python"
test -x "$service_python"
"$service_python" -m powerpoint_extraction \
  >"${EX1_CAPACITY_OUTPUT}.server.log" 2>&1 &
service_pid=$!

cleanup() {
  kill -TERM "$service_pid" 2>/dev/null || true
  wait "$service_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 300); do
  if curl -fsS "http://127.0.0.1:${ex1_port}/health" | grep -q '"ready":true'; then
    break
  fi
  kill -0 "$service_pid"
  sleep 0.1
done
curl -fsS "http://127.0.0.1:${ex1_port}/health" | grep -q '"ready":true'

"$service_python" probes/capacity.py \
  --url "http://127.0.0.1:${ex1_port}" \
  --output "$EX1_CAPACITY_OUTPUT" \
  --service-pid "$service_pid" \
  --data-dir "$EX1_DATA_DIR" \
  --conditions "fresh service and data directory; five serial repeat runs before 100 serial decks; OS page cache not flushed; no comparative baseline"
