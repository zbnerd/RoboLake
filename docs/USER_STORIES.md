# RoboLake v0.1 User Stories

## Personas and constraints

The **researcher** operates the CLI from a machine that can read a selected local dataset directory.
The **operator** maintains one trusted RoboLake deployment. Files are opaque; neither persona asks
RoboLake to parse or prepare data for training.

Priority labels are `P0` (required for v0.1) and `P1` (release hardening within v0.1).

## Researcher stories

### US-01 — Scan a directory (`P0`)

As a researcher, I can scan `./demo-dataset` and write a manifest so I can inspect exactly what will
be transferred.

**Acceptance criteria**

- Every regular file has a POSIX-style relative path, byte size, SHA-256, and optional media type.
- Entries are sorted by normalized relative path before canonical serialization.
- Repeating the scan without changes produces identical manifest bytes and hash.
- Absolute paths never appear in the manifest.

### US-02 — Fail safely on an unstable or unsafe tree (`P0`)

As a researcher, I receive an actionable error rather than an ambiguous manifest when the source
contains a symlink, unsafe path, duplicate normalized path, non-regular file, or a file that changes
while being hashed.

### US-03 — Register a dataset and version (`P0`)

As a researcher, I can register a dataset name and its manifest so RoboLake assigns a monotonically
increasing version and reports which blobs already exist.

**Acceptance criteria**

- Retrying with the same idempotency key returns the original dataset or version.
- Reusing a version-registration key with different content returns a conflict.
- Entries and content identity cannot change after registration; only lifecycle state can advance.

### US-04 — Upload without permanent storage credentials (`P0`)

As a researcher, I can upload through short-lived presigned requests so the CLI never stores MinIO
or cloud object-storage credentials.

- Small files use a presigned single PUT; large files use multipart upload.
- Progress shows files and bytes completed without printing presigned URLs.
- The API does not receive file bodies.

### US-05 — Resume an interrupted upload (`P0`)

As a researcher, I can rerun upload after interruption and continue from the confirmed parts.

- The API reconciles PostgreSQL with `ListParts` before issuing replacement URLs.
- Confirmed parts with matching number, size, ETag, and checksum are skipped.
- Expired URLs are replaceable without creating a new dataset version.
- A missing or aborted provider upload starts a replacement session while preserving the version.

### US-06 — Reuse verified content (`P0`)

As a researcher, I do not re-upload bytes when a blob with the same SHA-256 and size is already
verified and available. Hash equality with a size mismatch is treated as an integrity error.

### US-07 — Finalize and verify (`P0`)

As a researcher, I can request finalization and see the version move through `VERIFYING` to `READY`
only after RoboLake streams every new object and matches its manifest SHA-256 and size.

- A mismatch marks the blob and version failed and never publishes `READY`.
- Retrying finalization after success returns the existing `READY` result.
- Retrying after a transient read failure rechecks unresolved blobs.

### US-08 — Inspect status (`P0`)

As a researcher, I can view version state, total and completed bytes, pending files, failed sessions,
and the next recovery action without reading database or object-store internals.

### US-09 — Download a version (`P0`)

As a researcher, I can download a `READY` version to a new destination and reconstruct the manifest
tree.

- The CLI refuses non-`READY` versions and unsafe manifest paths.
- Each file is written to a temporary sibling, checked for size and SHA-256, then atomically renamed.
- Existing destination files are not silently overwritten.
- Failed downloads leave no file presented as complete.

### US-10 — Understand the CLI (`P1`)

As a researcher, every public command has useful `--help`, stable nonzero exit codes, and errors that
identify the affected logical path or session without exposing credentials or presigned URLs.

## Operator stories

### US-11 — Run a reproducible local environment (`P0`)

As an operator, I can start the API, PostgreSQL, MinIO, and bucket initialization with Docker Compose,
using only documented `.env.example` values and health checks.

### US-12 — Clean abandoned uploads (`P1`)

As an operator, I can identify expired upload sessions, abort their provider multipart uploads, and
mark them `ABORTED` without affecting completed blobs. A storage-side stale-upload policy provides a
second cleanup layer.

### US-13 — Diagnose a transfer (`P1`)

As an operator, structured logs correlate dataset version, upload session, blob hash prefix, and
request ID while excluding local absolute paths, credentials, and presigned query strings.

## v0.1 definition of done

All P0 stories pass automated unit or integration acceptance tests. P1 stories pass before the v0.1
release candidate. The end-to-end release scenario scans, registers, interrupts, resumes, verifies,
downloads, and compares a synthetic multi-gigabyte dataset.
