#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
workspace=$(mktemp -d "${TMPDIR:-/tmp}/robolake-demo.XXXXXX")
source_dir="$workspace/demo-dataset"
restored_dir="$workspace/restored"

cleanup() {
  rm -rf "$workspace"
}
trap cleanup EXIT

cd "$repo_root"
docker compose up -d --build --wait
uv sync
uv run alembic upgrade head

uv run robolake example generate "$source_dir" --seed 7
reference=$(
  uv run robolake push "$source_dir" --dataset demo/pick-place \
    | awk '/^READY / { print $2 }'
)
test -n "$reference"

uv run robolake status "$reference"
uv run robolake manifest "$reference"
printf '\n'
uv run robolake pull "$reference" --output "$restored_dir"

diff -qr "$source_dir" "$restored_dir"
(
  cd "$source_dir"
  find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$workspace/source.sha256"
(
  cd "$restored_dir"
  find . -type f -print0 | sort -z | xargs -0 sha256sum
) > "$workspace/restored.sha256"
diff -u "$workspace/source.sha256" "$workspace/restored.sha256"

printf 'RoboLake v0.1 demo verified: %s\n' "$reference"
