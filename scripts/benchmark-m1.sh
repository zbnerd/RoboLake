#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
mode=${1:-quick}

case "$mode" in
  quick | manual-large) ;;
  *)
    printf 'usage: %s [quick|manual-large]\n' "$0" >&2
    exit 2
    ;;
esac

run_id=${ROBOLAKE_BENCHMARK_RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}
output=${ROBOLAKE_BENCHMARK_OUTPUT:-/tmp/robolake-m1-${mode}-${run_id}.json}
manual_large_bytes=${ROBOLAKE_MANUAL_LARGE_BYTES:-4000000000}
workspace=$(mktemp -d "${TMPDIR:-/tmp}/robolake-benchmark.XXXXXX")

cleanup() {
  rm -rf "$workspace"
}
trap cleanup EXIT

cd "$repo_root"
uv sync --locked
docker compose up -d --wait
uv run alembic upgrade head
uv run python scripts/benchmark_m1.py \
  --mode "$mode" \
  --workspace "$workspace/data" \
  --output "$output" \
  --run-id "$run_id" \
  --manual-large-bytes "$manual_large_bytes"

printf 'RoboLake M1 benchmark evidence: %s\n' "$output"
