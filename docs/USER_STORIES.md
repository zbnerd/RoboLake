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

- Every regular file has a POSIX-style relative path, byte size, and SHA-256; suffix-derived media
  hints and filesystem metadata are not canonical identity.
- Entries are sorted by normalized relative path before canonical serialization.
- Repeating the scan without changes produces identical manifest bytes and hash.
- Absolute paths never appear in the manifest.
- Nested empty directories are ignored; file/ancestor and exact/NFC/case-folded collisions fail.

### US-02 — Fail safely on an unstable or unsafe tree (`P0`)

As a researcher, I receive an actionable error rather than an ambiguous manifest when the source
contains a symlink, unsafe path, duplicate normalized path, non-regular file, or a file that changes
while being hashed. M1 officially supports Linux/macOS filesystem behavior and safely escapes
untrusted path text in CLI output.

### US-03 — Register a dataset and version (`P0`)

As a researcher, I can register a dataset name and its manifest so RoboLake assigns a monotonically
increasing version and reports which blobs already exist.

**Acceptance criteria**

- Retrying with the same idempotency key returns the original dataset or version.
- Reusing a version-registration key with different content returns a conflict.
- Entries and content identity cannot change after registration; only lifecycle state can advance.
- Version numbers represent immutable-manifest registration order, not READY order. Changed content
  creates a new number while an older non-READY version remains recoverable.

### US-04 — Upload without permanent storage credentials (`P0`)

As a researcher, I can upload through short-lived presigned requests so the CLI never stores MinIO
or cloud object-storage credentials.

- M1 files at or below 5,000,000,000 bytes use a create-only single PUT signed for exact length,
  SHA-256, and `If-None-Match: *`; larger files are rejected before persistent mutation. M2 adds
  multipart.
- Progress separates logical files/bytes, unique Blob content, and invocation-created/reused Blobs
  without claiming exact wire bytes.
- The API does not receive file bodies.

### US-05 — Resume an interrupted upload (`P0`)

As a researcher, I can rerun an M1 upload after interruption and continue from confirmed whole
Blobs.

- The API reconciles the deterministic key using provider system SHA-256/size and a full-GET
  fallback only when the system checksum is absent.
- Confirmed `AVAILABLE` Blobs are skipped and reused as verification attestations.
- Expired URLs are replaceable without creating a new dataset version.
- Interrupted single PUT retransmits that Blob; M2 adds within-file part reconciliation.

### US-06 — Reuse verified content (`P0`)

As a researcher, I do not re-upload bytes when a blob with the same SHA-256 and size is already
verified and available. Hash equality with a size mismatch is treated as an integrity error.

- Concurrent `200`, `412`, and `409` conditional-write outcomes converge by API reconciliation.
- A poisoned existing key is reported with operator action and is never automatically overwritten
  or deleted.

### US-07 — Finalize and verify (`P0`)

As a researcher, I can request finalization and see the version move through `VERIFYING` to `READY`
only after every referenced Blob has reached `AVAILABLE` through the verification contract.

- A stored-object mismatch marks the Blob/version failed, derives `CONTACT_OPERATOR`, and never
  publishes `READY` or mutates storage.
- Retrying finalization after success returns the existing `READY` result.
- Transient/ambiguous failures leave progress resumable rather than inventing a terminal result.

### US-08 — Inspect status (`P0`)

As a researcher, I can view version state, logical snapshot totals/readiness, unique content
totals/availability, failed sessions, and a derived next action without reading internals.

### US-09 — Download a version (`P0`)

As a researcher, I can download a `READY` version to a new destination and reconstruct the manifest
tree.

- The CLI refuses non-`READY` versions and unsafe manifest paths.
- The API emits one Version-bound ordinal capability at a time; cursor replay selects the same entry
  with a fresh URL and consumes no server state.
- Each file is written under a private staging tree, checked for size/SHA-256, then atomically placed.
- The complete tree uses Linux/macOS atomic no-replace publication; existing destinations are never
  replaced and unsupported atomic primitives fail closed.
- Failed downloads leave no final output. M1 guarantees atomic visibility, not power-loss durability
  or pull resume.

### US-10 — Understand the CLI (`P1`)

As a researcher, every public command has useful `--help`, stable nonzero exit codes, and errors that
identify the affected logical path or session without exposing credentials or presigned URLs.

## Operator stories

### US-11 — Run a reproducible local environment (`P0`)

As an operator, I can start the API, PostgreSQL, MinIO, and bucket initialization with Docker Compose,
using only documented `.env.example` values and health checks.

### US-12 — Clean abandoned uploads (`P1`)

As an operator, after M2 introduces multipart, I can identify expired sessions, abort provider uploads, and
mark them `ABORTED` without affecting completed blobs. A storage-side stale-upload policy provides a
second cleanup layer.

### US-13 — Diagnose a transfer (`P1`)

As an operator, structured logs correlate dataset version, upload session, blob hash prefix, and
request ID while excluding local absolute paths, credentials, and presigned query strings.

## v0.1 definition of done

All P0 stories pass automated unit or integration acceptance tests. P1 stories pass before the v0.1
release candidate. The end-to-end release scenario scans, registers, interrupts, resumes, verifies,
downloads, and compares a synthetic multi-gigabyte dataset.
