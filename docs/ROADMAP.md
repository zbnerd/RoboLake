# RoboLake v0.1 Roadmap

## Delivery model

Milestones are ordered vertical slices. Each milestone must leave the repository formatted, typed,
tested, and documented. A milestone is complete only when its acceptance criteria are demonstrated;
later milestones must not be pulled forward as speculative infrastructure.

## M0 — Contracts and local foundation

**Status:** Complete — 2026-07-10

**Goal:** Make the approved v0.1 design runnable as a development skeleton.

**Scope**

- Adopt the modular-monolith package boundaries in [the architecture](ARCHITECTURE_V0_1.md).
- Add Typer, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL driver, S3 client, mypy, and Compose
  configuration using pinned compatible ranges.
- Provide `.env.example`, API/CLI entry points, PostgreSQL and MinIO health checks, and bucket init.
- Establish unit and integration pytest markers and CI commands.

**Acceptance criteria**

- `docker compose up --wait` produces healthy PostgreSQL, MinIO, and API services from a clean clone.
- `robolake --help`, API `/health`, Alembic upgrade, Ruff, mypy, and unit tests succeed; smoke
  integration tests prove a PostgreSQL transaction and MinIO create/head/abort cycle.
- Domain and application packages have automated import-boundary checks prohibiting FastAPI,
  Typer, SQLAlchemy, and S3 SDK imports.
- No dataset-transfer behavior or excluded technology is introduced.

## M1 — Deterministic manifest slice

**Goal:** Turn a local directory into a safe, reproducible content contract.

**Scope**

- Implement manifest domain types, canonical serialization, media-type policy, and SHA-256 hashing.
- Add `robolake dataset scan SOURCE --output manifest.json`.
- Reject symlinks, special files, path escapes, normalized-path collisions, and changed files.

**Acceptance criteria**

- Golden tests prove identical bytes and hash across shuffled traversal orders.
- Unit tests cover empty files, Unicode NFC paths, nested trees, large streamed files, mutation during
  scan, symlinks, Windows-style absolute/drive paths, `..`, NUL, and duplicate normalized paths.
- A scan never records an absolute source path and memory use is independent of individual file size.
- CLI output identifies counts, total bytes, manifest hash, and validation failures.

## M2 — Registry and immutable versions

**Goal:** Register manifests in PostgreSQL with explicit invariants.

**Scope**

- Add Dataset, DatasetVersion, DatasetEntry, Blob, idempotency-record tables, constraints, and Alembic
  migration.
- Implement create/read dataset, register/read version, manifest, and status API operations.
- Add CLI register and status commands.

**Acceptance criteria**

- Concurrent registrations assign unique monotonic version numbers per dataset.
- Repeated requests with the same key and payload return the same resource; a changed payload returns
  `409 Conflict`.
- Database constraints reject duplicate dataset names, version numbers, relative paths, invalid
  hashes, negative sizes, and invalid lifecycle states.
- Tests prove an entry set cannot be altered after registration and a `READY` version cannot regress.
- Integration tests run against PostgreSQL, not SQLite.

## M3 — Reliable direct upload and resume

**Goal:** Transfer opaque files to content-addressed object keys without routing bytes through API.

**Scope**

- Add UploadSession and UploadPart persistence and state transitions.
- Issue short-lived presigned single PUTs below 64 MiB and multipart URLs at or above 64 MiB.
- Reconcile resumptions with S3 `ListParts`; refresh URLs and complete or abort sessions idempotently.
- Skip blobs already verified as available.

**Acceptance criteria**

- API access logs and tests prove file bodies travel CLI-to-MinIO only.
- Killing the CLI after arbitrary parts, then rerunning it, sends only missing/mismatched parts.
- Part size is at least 5 MiB, at most 5 GiB, and dynamically large enough to stay within 10,000
  parts; all non-final parts are equal-sized.
- URL expiry, duplicate part acknowledgement, duplicate completion, `NoSuchUpload`, timeout, and
  retryable 5xx paths have deterministic recovery tests.
- Explicit abort and stale-session cleanup remove incomplete provider uploads and preserve completed
  objects.

## M4 — Verification, publication, and safe download

**Goal:** Publish only byte-verified versions and reconstruct them safely.

**Scope**

- Implement idempotent version finalization and server-side full-object SHA-256 verification.
- Add presigned GET download planning and CLI reconstruction using temporary files.
- Enforce `DRAFT -> UPLOADING -> VERIFYING -> READY | FAILED`, explicit
  `FAILED -> UPLOADING` repair, and blob availability rules. `READY` remains terminal.

**Acceptance criteria**

- Corrupt, truncated, reordered, or wrong-key objects can never make a version `READY`.
- Verification can be retried after transport failure without changing manifest identity.
- Download refuses non-`READY` versions, symlinks, destination escapes, collisions, and silent
  overwrites.
- Interrupted or corrupted downloads expose no completed file; successful files match manifest size
  and SHA-256 before atomic placement.
- An integration test proves two logical paths and versions can safely reuse one verified blob.

## M5 — Release hardening and v0.1 evidence

**Goal:** Demonstrate the observed workflow reliably and document its operating limits.

**Scope**

- Exercise the threat model, failure matrix, cleanup, observability, and operator runbook.
- Measure scan, upload, verification, and download behavior using synthetic small-file and large-file
  datasets.
- Finish public CLI/API documentation and upgrade/recovery instructions.

**Acceptance criteria**

- The complete workflow passes from a clean environment with a synthetic dataset containing nested
  paths, duplicate content, many small files, and at least one 6 GiB file.
- Forced interruption during multipart upload resumes correctly; forced corruption blocks `READY`;
  download reproduces the manifest exactly.
- Ruff, formatting, mypy, unit tests, PostgreSQL/MinIO integration tests, and threat regression tests
  pass in CI.
- Logs contain correlation identifiers but no presigned URLs, credentials, or absolute local paths.
- README and operator documentation state measured limits and all v0.1 non-goals.

## Beyond v0.1

No next product capability is selected here. After v0.1 is used in the observed workflow, collect
evidence about transfer scale, failures, and the actual downstream consumer before proposing another
scope or architecture decision.
