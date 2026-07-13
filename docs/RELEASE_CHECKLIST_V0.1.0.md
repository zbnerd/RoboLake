# RoboLake v0.1.0 Release Checklist

Run this checklist from a clean clone. It separates verification, tag creation, GitHub Release, and
rollback as distinct operator actions. Completing the checklist does not itself authorize any
remote mutation.

## 1. Clean source state

- [ ] Fetch authoritative refs without rewriting local work:

  ```bash
  git fetch origin --tags
  git switch develop
  git merge --ff-only origin/develop
  git status --short --branch
  git rev-parse HEAD
  ```

- [ ] Confirm the worktree is clean and HEAD is the release-approved commit.
- [ ] Confirm `git tag --list v0.1.0` is empty before creating the first tag.
- [ ] Record `docker version`, `docker compose version`, `uv --version`, and `python --version`.

## 2. Locked dependencies and package metadata

- [ ] Install exactly the locked environment:

  ```bash
  uv sync --locked
  ```

- [ ] Verify all version sources agree:

  ```bash
  uv run robolake version
  uv run python -c 'import importlib.metadata, robolake; print(robolake.__version__); print(importlib.metadata.version("robolake"))'
  rg -n '^version = "0\.1\.0"$' pyproject.toml
  ```

- [ ] Build local smoke artifacts and inspect metadata/content:

  ```bash
  rm -rf dist
  uv build
  unzip -p dist/robolake-0.1.0-py3-none-any.whl robolake-0.1.0.dist-info/METADATA
  unzip -l dist/robolake-0.1.0-py3-none-any.whl
  tar -tzf dist/robolake-0.1.0.tar.gz
  ```

- [ ] Confirm package name/version, Python `>=3.12,<3.13`, MIT license, README, CLI entry
  point, and runtime dependencies are present; confirm tests, `.env`, caches, and generated datasets
  are absent.
- [ ] Treat these artifacts as metadata/CLI smoke evidence only. v0.1.0 is distributed from the
  GitHub source archive and Docker Compose checkout; do not publish the wheel or sdist as standalone
  server deployment artifacts because they do not carry Alembic and Compose deployment assets.

## 3. Migration verification

Use temporary databases rather than deleting the normal development volume.

- [ ] Start dependencies without relying on an already migrated API database:

  ```bash
  docker compose up -d postgres minio minio-init --wait
  ```

- [ ] Create an empty temporary database and upgrade directly to head:

  ```bash
  docker compose exec -T postgres createdb -U robolake robolake_release_empty
  ROBOLAKE_DATABASE_URL='postgresql+psycopg://robolake:robolake-postgres-dev-only@localhost:15432/robolake_release_empty' \
    uv run alembic upgrade head
  ROBOLAKE_DATABASE_URL='postgresql+psycopg://robolake:robolake-postgres-dev-only@localhost:15432/robolake_release_empty' \
    uv run alembic current
  ```

- [ ] Create another temporary database, stop at M0, and upgrade to M1:

  ```bash
  docker compose exec -T postgres createdb -U robolake robolake_release_upgrade
  ROBOLAKE_DATABASE_URL='postgresql+psycopg://robolake:robolake-postgres-dev-only@localhost:15432/robolake_release_upgrade' \
    uv run alembic upgrade 20260710_0001
  ROBOLAKE_DATABASE_URL='postgresql+psycopg://robolake:robolake-postgres-dev-only@localhost:15432/robolake_release_upgrade' \
    uv run alembic upgrade head
  ```

- [ ] Confirm both report `20260711_0002 (head)`, then remove only the temporary databases:

  ```bash
  docker compose exec -T postgres dropdb -U robolake robolake_release_empty
  docker compose exec -T postgres dropdb -U robolake robolake_release_upgrade
  ```

## 4. Static analysis and tests

- [ ] Run every local release gate without suppressing warnings:

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy robolake scripts/benchmark_m1.py
  uv run pytest
  ```

- [ ] Record exact test count, failures, coverage, and every warning classification.
- [ ] Run the Linux filesystem contract exactly as CI does:

  ```bash
  uv run pytest \
    tests/unit/domain \
    tests/unit/infrastructure/test_scanner.py \
    tests/unit/infrastructure/test_atomic_tree.py \
    tests/unit/infrastructure/test_atomic_publish.py
  ```

- [ ] Confirm the GitHub Ubuntu and macOS jobs pass for the release-approved commit:

  ```bash
  gh run list --commit "$(git rev-parse HEAD)" --json workflowName,status,conclusion,url
  ```

## 5. Product workflows and documentation

- [ ] Run the end-to-end synthetic workflow:

  ```bash
  scripts/demo-v01.sh
  ```

- [ ] Run the bounded performance baseline and inspect the JSON:

  ```bash
  scripts/benchmark-m1.sh quick
  ```

- [ ] Follow the README ten-minute quickstart from a new output path.
- [ ] Check README Mermaid rendering and all relative documentation links on GitHub.
- [ ] Review `CHANGELOG.md`, `docs/RELEASE_NOTES_V0.1.0.md`,
  `docs/KNOWN_LIMITATIONS_V0.1.0.md`, and `docs/reports/M1_BASELINE.md` against measured evidence.

## 6. Secret and confidentiality scan

- [ ] Confirm `.env` is ignored and only synthetic `.env.example` values are tracked:

  ```bash
  git check-ignore .env
  git ls-files | rg '(^|/)\.env$' && exit 1 || true
  ```

- [ ] Run the organization's approved secret scanner over Git history and the current worktree. If
  Gitleaks is available:

  ```bash
  gitleaks git --redact --no-banner .
  gitleaks dir --redact --no-banner .
  ```

- [ ] Independently scan tracked/release files for common credential and private-key forms:

  ```bash
  rg --hidden \
    -g '!.git/**' -g '!.venv/**' -g '!dist/**' \
    'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}' .
  ```

- [ ] Review examples, docs, tests, benchmark output, and Git history for employer/internal names,
  NAS paths, screenshots, proprietary data, real hostnames, and presigned query strings.

## 7. Tag approval gate

- [ ] Confirm no release blocker remains and obtain explicit release approval.
- [ ] Confirm HEAD one last time and create an annotated local tag:

  ```bash
  git status --short --branch
  git rev-parse HEAD
  git tag -a v0.1.0 -m 'RoboLake v0.1.0'
  git show --no-patch --decorate v0.1.0
  ```

- [ ] Push only after separate explicit authorization:

  ```bash
  git push origin v0.1.0
  ```

- [ ] Create a GitHub Release only after the tag is visible and separately authorized:

  ```bash
  gh release create v0.1.0 \
    --title 'RoboLake v0.1.0' \
    --notes-file docs/RELEASE_NOTES_V0.1.0.md
  ```

## 8. Rollback and correction

- [ ] If the tag exists only locally and was not approved/pushed, delete only the local tag:

  ```bash
  git tag -d v0.1.0
  ```

- [ ] If an incorrect remote tag or GitHub Release was published but no consumer has used it, stop,
  obtain explicit destructive-action approval, remove the GitHub Release first, then the remote tag.
  Never reuse `v0.1.0` after public consumption.
- [ ] If consumers may have used the release, preserve history and publish a corrected `v0.1.1`.
- [ ] For deployment rollback, stop writers, restore the pre-upgrade PostgreSQL backup and matching
  object-storage state, and redeploy the previous application image. Do not infer registry state
  from object keys and do not run the destructive Alembic downgrade against shared data without a
  tested restore plan.

## Current release-readiness evidence

On 2026-07-13, the release-readiness branch verified dependency sync, empty and M0 migrations, 237
tests with 87% coverage, 103 Linux filesystem-contract tests, the synthetic demo, package build, and
successful Ubuntu/macOS PR checks plus the merge-commit workflow. GitHub secret scanning reported
zero open alerts; a high-confidence credential-pattern scan found none in the worktree or Git
history. The optional 4 GB profile reached READY and completed verified pull with 51.965 MiB peak
process RSS. One visible upstream TestClient deprecation warning is a follow-up issue. The tag,
GitHub Release, remote branch, and PR remain uncreated.
