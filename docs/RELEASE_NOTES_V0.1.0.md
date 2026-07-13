# RoboLake v0.1.0 Release Notes

RoboLake v0.1.0 is the first public release of an immutable dataset registry for large robotics
data. It addresses one observed workflow: safely moving a selected regular-file dataset tree from a
local workstation or NAS into S3-compatible storage and reconstructing it elsewhere with provenance
and byte integrity intact.

## Highlights

- Canonical schema-v1 manifests contain only normalized relative path, size, and SHA-256.
- DatasetVersion identity is immutable from registration; READY is terminal.
- Blob keys are globally content-addressed and shared across logical paths and Versions.
- Push is idempotent and resumes at verified whole-Blob boundaries.
- Conditional `200`, `412`, and ambiguous `409` outcomes reconcile through one deterministic key.
- Pull verifies every byte and publishes the complete tree atomically without replacing an existing
  destination.
- Empty source roots publish as valid empty READY Versions.

## Quick verification

```bash
uv sync --locked
docker compose up -d --build --wait
uv run alembic upgrade head
scripts/demo-v01.sh
```

The release-readiness baseline on 2026-07-13 produced:

- empty database migration: `20260710_0001 -> 20260711_0002`
- explicit M0 upgrade: `20260710_0001 -> 20260711_0002`
- full release-readiness suite: 237 passed, 87% branch-aware coverage (the merged M1 baseline was
  228 tests; 9 benchmark-harness tests were added)
- Linux filesystem contract: 103 passed
- PR #3 Ubuntu and macOS filesystem-contract jobs: passed
- merge commit `9b5d06a` push workflow: passed
- synthetic push/pull demo with checksum and directory comparison: passed
- optional 4 GB manual profile: READY, verified pull, 51.965 MiB peak process RSS

One upstream FastAPI/Starlette TestClient deprecation warning remains visible. It does not execute in
the runtime transfer path and is classified as a non-blocking follow-up dependency issue.

## Upgrade from M0

Back up PostgreSQL before any upgrade, preserve object-storage data, and apply:

```bash
uv sync --locked
uv run alembic upgrade head
uv run alembic current
```

The expected current revision is `20260711_0002 (head)`. The migration adds registry tables,
constraints, and immutability/state-transition triggers. Review
[the release checklist](RELEASE_CHECKLIST_V0.1.0.md) before tagging or upgrading shared state.

## Compatibility and scope

- MIT License
- Python 3.12 only
- supported local filesystem contract: Linux and macOS
- PostgreSQL 17 and the pinned MinIO image are the verified local baseline
- S3-compatible providers must pass the conditional-write and checksum contract
- single PUT only, maximum 5,000,000,000 bytes per file
- trusted-network deployment only; authentication and multi-tenancy are absent
- source-checkout and Docker Compose distribution only; the locally buildable Python wheel is not a
  standalone server deployment artifact and is not published for v0.1.0

See [known limitations](KNOWN_LIMITATIONS_V0.1.0.md), the
[security summary](SECURITY_MODEL_SUMMARY.md), and the [ADR index](ADR_INDEX.md).

## Included history

- M1 PR: [#3](https://github.com/zbnerd/RoboLake/pull/3)
- M1 implementation: `79362be7d5c83f18eabddce5394dd1c8cda97d8d`
- M1 merge: `9b5d06ac63940e5f871cd83a9128007735f9ed1a`

No tag or GitHub Release is created by the release-readiness task. Those actions require explicit
approval after the checklist is complete.
