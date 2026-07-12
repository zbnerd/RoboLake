# RoboLake — Codex Project Instructions

## Product definition

RoboLake is an open-source robot dataset transfer and registry platform.

The first real-world problem we are solving is:

"Robot teleoperation datasets are stored on NAS or local workstations and are manually copied to
training servers. Uploads may fail, be duplicated, lose provenance, or become difficult to track."

RoboLake v0.1 must solve only this problem:

- scan a local dataset directory
- create a deterministic manifest
- upload its files safely to S3-compatible object storage
- verify data integrity
- register dataset metadata and immutable versions
- resume interrupted uploads
- download a version and reconstruct the original directory

## Product principles

- Solve one observed problem at a time.
- Build vertical slices, not speculative infrastructure.
- Prefer correctness over convenience.
- Make immutable resources and create-only storage operations the default.
- Detect, report, and stop when safe automatic recovery cannot be proven.
- CLI usability is a first-class requirement.
- Dataset-version manifests are immutable from registration; READY is terminal.
- Upload and completion operations must be idempotent.
- Raw files belong in object storage.
- Searchable metadata belongs in PostgreSQL.
- Logical paths and physical object keys must be separated.
- Store blobs by content hash when practical.
- Treat canonical manifests and SHA-256 byte identities as sources of truth.
- Every important state transition must be explicit and testable.

## Explicit non-goals for v0.1

Do NOT add any of the following unless a later issue and ADR explicitly require it:

- Kafka
- Airflow
- Apache Iceberg
- Parquet conversion
- ROS2 runtime integration
- MCAP semantic parsing
- LeRobot conversion
- model training
- model registry
- authentication or multi-tenancy
- frontend or dashboard
- Kubernetes
- microservices

Do not add technology solely to make the architecture look impressive.

## Technology stack

- Python 3.12
- FastAPI
- Typer CLI
- Pydantic v2
- SQLAlchemy 2
- Alembic
- PostgreSQL
- MinIO through the S3-compatible API
- pytest
- Ruff
- mypy
- Docker Compose
- pyproject.toml-based packaging

## Repository structure

Prefer a modular monolith:

- apps/api
- apps/cli
- robolake/domain
- robolake/application
- robolake/infrastructure
- tests/unit
- tests/integration
- docs/adr
- examples

The domain and application layers must not import FastAPI, SQLAlchemy, MinIO SDK, or Typer.

## Data model expectations

At minimum, model:

- Dataset
- DatasetVersion
- DatasetEntry
- Blob
- UploadSession
- UploadPart, beginning with multipart support in M2

Suggested state transitions:

- DatasetVersion: DRAFT -> UPLOADING -> VERIFYING -> READY | FAILED
- M1 UploadSession: CREATED -> IN_PROGRESS -> COMPLETED | FAILED
- UploadSession ABORTED and UploadPart lifecycle begin with M2 multipart support

READY versions are immutable.

## Dataset manifest

A manifest entry should contain at least:

- relative_path
- size_bytes
- sha256

Do not place suffix-derived media types or other reproducible enrichment in the canonical manifest.
M1 snapshot identity is limited to schema version, normalized regular-file paths, sizes, and bytes.

The manifest itself must have a deterministic hash independent of filesystem traversal order.

Never include absolute local filesystem paths in the stored manifest.

## Security and confidentiality

- Use only synthetic or public test data.
- Never include employer names, internal server names, NAS paths, credentials, schemas, screenshots,
  or proprietary sample data.
- Never hard-code credentials.
- Provide .env.example only.
- Reject path traversal and unsafe relative paths.
- Validate downloaded content before placing it on disk.
- Treat presigned URLs as bearer capabilities and never log their query strings.
- M1 filesystem behavior is supported and contract-tested on Linux and macOS; Windows runtime and
  Windows filename semantics are not supported yet.

## Engineering quality

- Use complete type annotations.
- Use domain-specific exceptions.
- Do not catch Exception unless translating at an application boundary.
- Add database constraints, not only application checks.
- Add Alembic migrations for every schema change.
- Unit-test domain behavior.
- Integration-test PostgreSQL and MinIO behavior.
- Prefer idempotent APIs.
- Keep functions small and behavior explicit.
- Public CLI commands must have useful --help output.
- Update README and architecture documentation when behavior changes.

## Working protocol

Before editing:

1. Inspect the repository and existing documentation.
2. State assumptions.
3. Produce a short implementation plan.
4. Ask only questions that block implementation.

While working:

1. Implement one milestone at a time.
2. Do not expand scope without explaining why.
3. Run formatting, static analysis, unit tests, and integration tests.
4. Fix failures rather than merely reporting them.

At the end of each task, report:

- files changed
- decisions made
- commands executed
- test results
- known limitations
- recommended next milestone

Do not create commits unless explicitly requested.
