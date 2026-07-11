# M1 Complete Vertical Slice Design

- **Status:** Approved design
- **Date:** 2026-07-11
- **Target:** RoboLake M1

## 1. Decision summary

M1 delivers one complete synchronous workflow: scan a local directory, register an immutable
dataset version, upload missing content-addressed blobs with presigned single PUTs, verify stored
bytes, publish the version as `READY`, inspect it, and reconstruct it safely.

This replaces the old roadmap split that limited M1 to manifest generation. Dataset registration,
single-PUT transfer, verification, and download move into M1 because the milestone must demonstrate
an end-to-end product outcome. True multipart upload and within-file resume move to M2. M1 persists
resume progress at whole-file granularity.

The architecture remains a modular monolith. The FastAPI service owns metadata, lifecycle,
idempotency, storage control operations, and server-side verification. The Typer CLI scans local
files and transfers bytes directly to MinIO through short-lived presigned URLs. File bodies never
pass through the API, and the CLI never receives permanent object-storage credentials.

## 2. Goals and non-goals

### Goals

- Provide `push`, `status`, `manifest`, and `pull` as a usable vertical slice.
- Treat a canonical manifest as the immutable identity of a dataset version.
- Deduplicate physical bytes by SHA-256 while preserving logical paths in PostgreSQL.
- Resume an interrupted push without retransmitting blobs already verified or recoverable from the
  deterministic object key.
- Publish only after the API independently verifies full-file byte count and SHA-256.
- Reconstruct a `READY` version without traversal, overwrite, or partial-output hazards.
- Cover the workflow with unit, PostgreSQL, MinIO, API, CLI, and executable demo evidence.

### Non-goals

- Multipart upload or within-file resume
- Deletion, retention, garbage collection, or persisted Blob reference counts
- Continuous object scrubbing or a post-publication `CORRUPTED` lifecycle
- Authentication, multi-tenancy, frontend, or background workers
- Kafka, Airflow, Iceberg, Parquet, ROS2, MCAP parsing, conversion, or training behavior
- Empty-directory preservation below the selected root, filesystem metadata, hard links, or
  symlink reconstruction

## 3. Public CLI contract

```bash
robolake push ./examples/demo-dataset --dataset demo/pick-place
robolake status demo/pick-place@v1
robolake manifest demo/pick-place@v1
robolake pull demo/pick-place@v1 --output /tmp/robolake-restored
```

`push` scans, registers, transfers, verifies, and finalizes synchronously. Success means the command
returns only after the resolved version is `READY`. It reports scan totals, the version reference,
uploaded and skipped files, and final state.

`status` reports state, logical file and byte totals, derived `is_empty`, verified and pending
logical files and bytes, safe failure information, and the next recovery action. Duplicate logical
entries that reference one Blob count separately in logical totals.

`manifest` writes the stored canonical JSON bytes directly to stdout. `pull` requires an output path
that does not yet exist and reports verified file and byte progress.

Dataset references split on the final `@v<positive integer>`. Dataset names are Unicode NFC text,
may contain `/` namespace separators, and reject control characters, `@`, leading or trailing `/`,
empty segments, and `.` or `..` segments. Names are metadata only and never enter filesystem paths
or object keys.

CLI configuration uses `ROBOLAKE_API_URL`. Public commands have useful `--help`; M1 does not add
`ls`, delete, GC, repair, auth, or multipart commands.

## 4. Component boundaries

```text
CLI ── control JSON ──> FastAPI ──> PostgreSQL
 │                         │
 └── presigned PUT/GET ──> MinIO
```

- `robolake/domain` owns manifest values, dataset/version/blob/session entities, state-transition
  rules, invariants, and domain-specific exceptions.
- `robolake/application` owns scan, registration, push, finalization, query, and pull orchestration
  expressed through typed ports.
- `robolake/infrastructure` implements PostgreSQL repositories, S3 control operations, filesystem
  scanning/download, HTTP transfer, canonical serialization, and settings.
- `apps/api` maps HTTP requests and domain failures to stable schemas and codes.
- `apps/cli` parses commands, renders progress, and maps application/API errors to exit codes.

Domain and application code do not import FastAPI, Typer, SQLAlchemy, boto3, or MinIO libraries.

## 5. Domain model

| Model | M1 responsibility and invariants |
| --- | --- |
| `Dataset` | Stable UUID and unique normalized logical name. Serializes version-number allocation. |
| `DatasetVersion` | Dataset-scoped version number, canonical manifest identity, totals, and publication workflow. Manifest and entries are immutable immediately after registration. |
| `DatasetEntry` | One safe relative POSIX path referencing one Blob, plus an optional deterministic media type. |
| `Blob` | Physical content identity and object lifecycle. UUID is the relational key; SHA-256 is unique content identity; size disagreement for one digest is an integrity conflict. |
| `UploadSession` | One persisted single-PUT attempt for a missing Blob, including state, receipt, and safe failure information. |
| `IdempotencyRecord` | Operation scope, caller key, canonical request fingerprint, and stable resource/response. |

`Blob` stores `id`, `sha256`, `size_bytes`, `object_key`, state, verification timestamps, creation
timestamp, and bounded failure fields. `media_type` stays on `DatasetEntry`: identical bytes can be
referenced through logical filenames with different extensions or meanings.

The object key is derived only from a lowercase digest:

```text
blobs/sha256/<first-2>/<next-2>/<64-char-sha256>
```

An `AVAILABLE` physical Blob is immutable. RoboLake never issues another write URL for it. M1 does
not store a reference count; a future retention policy can calculate reachability from
`DatasetEntry` without introducing a counter that can drift.

M1 does not create a fake `UploadPart(part_number=1)`. `UploadPart` enters the model with real
multipart receipt and reconciliation semantics in M2.

## 6. Manifest and scanner contract

Manifest schema version 1 is:

```json
{
  "schema_version": 1,
  "entries": [
    {
      "relative_path": "camera/front.mcap",
      "size_bytes": 123,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "media_type": "application/mcap"
    }
  ]
}
```

Canonicalization rules are contractual:

1. Normalize paths to Unicode NFC and POSIX `/` separators.
2. Reject absolute, drive-qualified, UNC, backslash-containing, NUL-containing, empty, `.`, and
   `..` segments.
3. Reject exact, NFC-normalized, and case-folded collisions.
4. Sort entries by UTF-8 bytes of `relative_path`.
5. Emit fields in schema order and omit `media_type` when unknown.
6. Use a repository-owned, versioned extension map rather than host MIME configuration.
7. Serialize UTF-8 with no BOM, indentation, insignificant whitespace, ASCII escaping, or trailing
   newline.
8. Hash exactly those canonical bytes with SHA-256.

The empty manifest is exactly:

```json
{"schema_version":1,"entries":[]}
```

The scanner rejects a symlink root, symlinked directory or file, and every non-regular file. It
walks without following links, validates every relative path, opens files without following a final
symlink where the platform supports it, and compares device, inode, size, and nanosecond mtime
before and after hashing. Hashing uses bounded chunks; memory use does not depend on file size.

The upload stream counts and hashes bytes again before acknowledgement. Source mutation therefore
cannot make a Blob `AVAILABLE`, even if it occurs after the initial scan. Absolute source paths,
timestamps, owners, and permissions never enter the manifest or API payload.

## 7. PostgreSQL schema and enforcement

### `datasets`

- `id uuid primary key`
- `name text not null unique` with non-empty and bounded-length checks
- `created_at timestamptz not null`

### `dataset_versions`

- UUID primary key and dataset foreign key
- positive `version_number`
- schema version, canonical `manifest_bytes bytea`, and lowercase 64-hex `manifest_sha256`
- state, nonnegative `file_count` and `total_bytes`
- bounded `failure_code` and `failure_detail`
- creation, sealing, verification, and ready timestamps
- unique `(dataset_id, version_number)` and `(dataset_id, manifest_sha256)`

Canonical bytes use `bytea`, not JSONB, because JSONB does not preserve canonical serialization.
`is_empty` is never persisted; API and CLI derive it from `file_count == 0`.

### `blobs`

- UUID primary key
- unique lowercase 64-hex SHA-256
- nonnegative size and unique deterministic object key
- constrained state and verification/failure timestamps

A duplicate SHA-256 with a different size is rejected as an integrity conflict rather than inserted
as another row.

### `dataset_entries`

- dataset-version and Blob foreign keys
- validated relative path and optional bounded media type
- primary key `(dataset_version_id, relative_path)`
- indexes on Blob and version/Blob references

### `upload_sessions`

- UUID primary key, Blob foreign key, and initiating-version foreign key
- strategy constrained to `SINGLE_PUT`
- lifecycle state, opaque ETag receipt, activity/completion timestamps, and bounded failure fields
- a partial unique index permits at most one active session per Blob

### `idempotency_records`

- primary key `(scope, key)`
- canonical request SHA-256
- resource type/UUID, HTTP status, stable response JSON, and timestamps

Database constraints validate states, hashes, counts, sizes, keys, and foreign references. Triggers
reject entry mutation or manifest-identity changes after sealing, illegal version/blob/session state
transitions, every semantic update to `READY`, and identity mutation of `AVAILABLE` Blobs. These are
last-line controls in addition to domain checks.

Registration inserts or resolves the Dataset, allocates a version under a Dataset row lock, writes
the version, entries, Blob upserts, sealing timestamp, and idempotency response in one transaction.
Each Blob upload/verification commits its own progress. Finalization locks the version and rechecks
entry count, byte total, canonical manifest hash, and Blob availability in one transaction.

## 8. State machines and failure semantics

```text
DatasetVersion:
  DRAFT -> UPLOADING -> VERIFYING -> READY
                               \--> FAILED
  FAILED -> UPLOADING

Blob:
  PENDING -> UPLOADING -> VERIFYING -> AVAILABLE
                                 \--> FAILED
  FAILED -> UPLOADING

UploadSession:
  CREATED -> IN_PROGRESS -> COMPLETED
                         \--> FAILED
  CREATED/IN_PROGRESS -> ABORTED
```

An empty version follows `DRAFT -> VERIFYING -> READY`; verification checks empty entries, zero
counts, zero bytes, and the canonical empty-manifest hash. `READY` is terminal.

The manifest and entries are immutable in every state. `FAILED` describes a recoverable publication
workflow, not mutable content identity. Rerunning `push` for the same source can transition the same
version through `FAILED -> UPLOADING -> VERIFYING -> READY` without modifying its manifest.

Only deterministic integrity outcomes such as `CHECKSUM_MISMATCH` or `SIZE_MISMATCH` move the
version to `FAILED`. Network, database, and temporary object-storage errors leave the version in its
current progress state because the outcome may be ambiguous and, for a database outage, cannot be
reliably recorded in that database. Stable `failure_code` values drive retry advice; separate
`FAILED_RECOVERABLE` and `FAILED_FATAL` states are not introduced.

## 9. Push and file-level resume

`push` executes these steps:

1. Validate and scan the complete source tree.
2. Produce canonical manifest bytes and digest.
3. Apply the M1 file-size preflight before any persistent operation.
4. Create/find the Dataset and register/find the manifest version idempotently.
5. Move a version with missing Blobs to `UPLOADING`.
6. For each unique manifest Blob, skip `AVAILABLE` or create/find its UploadSession.
7. Request a short-lived URL signed for the exact bucket, deterministic key, PUT method, content
   length, and checksum headers.
8. Stream the local file directly to MinIO while recounting and rehashing it.
9. Submit the opaque provider receipt; the API reconciles the deterministic object key and streams
   `GetObject` to calculate full SHA-256 and size independently.
10. Mark the session complete and Blob available only on an exact match.
11. Lock and finalize the version through `VERIFYING` to `READY` after every referenced Blob is
    available.

The CLI processes files sequentially in M1. Bounded concurrency is not needed to prove the product
semantics and can be added only after measurement.

The per-file M1 limit defaults to `5_000_000_000` bytes, matching the documented S3 single-PUT
limit. Files larger than the configured limit are reported by safe relative path and rejected before
Dataset creation, version registration, UploadSession creation, or storage writes. The error says
that multipart is an M2 feature. Aggregate dataset size is not capped by this rule; it applies to
each unique Blob. The limit is environment-configurable only downward for provider testing.

See the AWS integrity documentation for the current single-operation limit:
<https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html>.

### Duplicate and interrupted push

`(dataset_id, manifest_sha256)` identifies a version. A repeated unchanged push resolves the same
version; changed content creates the next version. CLI idempotency keys are deterministic per
operation and payload, while the API stores the canonical request fingerprint. Same key and same
payload replay the original response; same key and different payload returns conflict.

After interruption, the CLI rescans and must reproduce the registered manifest. For each
non-available Blob, the API inspects the deterministic key. If a PUT succeeded before the client
acknowledgement was persisted, the API streams and verifies that object, then marks it available
without retransmission. A missing or mismatching object is overwritten only while its Blob is not
`AVAILABLE`. Already verified Blobs are never presigned for write.

Two logical paths or versions with the same digest and size reference one Blob and one physical
object. A duplicate push of a `READY` version performs no storage write after resolving and checking
the same manifest.

## 10. Pull and reconstruction safety

The API issues download capabilities only for `READY` versions. The CLI fetches the sealed canonical
manifest, validates the entire path set again, and rejects an existing output path before creating
anything.

The CLI creates a private, uniquely named staging directory beside the intended output, ensuring the
same filesystem for the final rename. It obtains presigned GETs in bounded pages. Each object is
streamed into an exclusively created temporary sibling inside staging while computing size and
SHA-256. Only a match is atomically renamed to its logical staging path. After every entry succeeds,
the staging directory is atomically renamed to the final output.

An empty version atomically renames an empty staging directory. A handled failure removes temporary
state best-effort and never creates the final output. An uncatchable process kill may leave only a
hidden staging directory, never files presented at the requested output path.

Download revalidates containment and refuses symlink parents, normalized collisions, and existing
targets. It never silently overwrites. Duplicate logical entries may download the same Blob more
than once in M1; using hard links would change file semantics, and a local download cache is outside
the required slice.

`READY` means verified at publication time, not continuously scrubbed. If an operator mutates an
object afterward, pull detects the checksum/size mismatch, removes its temporary output, and returns
an integrity error. M1 does not regress the terminal version or introduce a persistent
`CORRUPTED` state; continuous availability repair requires a later decision.

## 11. API proposal

All endpoints live under `/v1` and exchange metadata/control JSON only.

| Method and path | Purpose |
| --- | --- |
| `POST /v1/datasets` | Idempotently create/find a normalized Dataset. |
| `POST /v1/datasets/{dataset_id}/versions` | Validate canonical manifest and register/find a sealed version. |
| `GET /v1/versions/resolve?dataset=...&version=...` | Resolve a CLI dataset reference without placing `/`-containing names in a path segment. |
| `GET /v1/versions/{version_id}` | Return state, totals, derived `is_empty`, progress, failure, and recovery advice. |
| `GET /v1/versions/{version_id}/manifest` | Return exact canonical bytes as `application/json`. |
| `POST /v1/versions/{version_id}/upload-sessions` | Get/create a session for one referenced Blob, or report it available. |
| `GET /v1/upload-sessions/{session_id}` | Return persisted/reconciled file-level state. |
| `POST /v1/upload-sessions/{session_id}/url` | Return a short-lived presigned PUT and required headers. |
| `POST /v1/upload-sessions/{session_id}/complete` | Reconcile, stream-verify, and persist completion idempotently. |
| `POST /v1/versions/{version_id}/finalize` | Verify aggregate invariants and publish idempotently. |
| `POST /v1/versions/{version_id}/download-plan` | Return a bounded page of presigned GET capabilities. |

Mutating create/register calls require `Idempotency-Key`; resource-targeted completion and
finalization calls are naturally idempotent. The API derives object keys from validated digests and
never accepts a client-supplied physical key.

The API owns an internal S3 client for control and verification plus a presigning client configured
with `ROBOLAKE_S3_PUBLIC_ENDPOINT_URL`. URL validity remains short and configurable. URLs and query
strings never enter logs, errors, database fields, or CLI arguments.

## 12. Error and security contract

API failures use a stable envelope:

```json
{
  "error": {
    "code": "CHECKSUM_MISMATCH",
    "message": "Blob verification failed for camera/front.mcap."
  }
}
```

Messages identify a logical relative path, version reference, or short digest prefix and give a
recovery action when one exists. They do not reveal an absolute source path, credential, presigned
URL/query, provider exception, database connection string, or dataset bytes. Free-text fields are
bounded and transport input is validated before application use.

HTTP mappings include 404 for missing references, 409 for idempotency/content conflicts, illegal
transitions, and `VERSION_IMMUTABLE`, 413 for bounded request/manifest limits, 422 for invalid
domain input, and 502/503 for safely translated dependency failures.

CLI exit codes are stable:

- `0`: success
- `2`: usage, local validation, unsafe path, or unsupported single-PUT size
- `3`: not found, conflict, or illegal state
- `4`: retryable API, network, or storage failure
- `5`: checksum or size integrity failure
- `1`: unexpected failure translated at the application boundary

The source root may be shown locally to its operator but is never sent to the API. The CLI protects
presigned bearer capabilities in memory and redacts HTTP diagnostics. Registration revalidates
canonical manifest rules server-side rather than trusting CLI output.

## 13. Test strategy

### Domain and application unit tests

- Golden canonical bytes and digest independent of file creation/traversal order
- Empty and zero-byte files, Unicode NFC paths, nested paths, unknown media types, and large
  chunked reads
- Symlinks, special files, absolute/drive/UNC paths, separators, NUL, dot segments, normalized and
  case-fold collisions, and source mutation
- Dataset/version/blob/session state-transition tables and failure-code retry policy
- Empty-version invariants, duplicate manifest identity, object-key derivation, and size conflicts
- Pull preflight, containment, existing-output refusal, temporary-file cleanup, and atomic rename
- Stable API exception mapping, CLI exit codes, useful messages, and secret/URL/path redaction
- Architecture import-boundary enforcement

### PostgreSQL integration tests

- Alembic upgrade from an empty database and downgrade
- Unique/check/foreign-key constraints and state/immutability triggers
- Concurrent version allocation and duplicate registration/idempotency races
- Atomic registration rollback and idempotent finalization
- `READY` and sealed-entry mutation rejection

### MinIO and end-to-end integration tests

- Presigned PUT/GET using public URLs while API verification uses internal credentials
- Duplicate push returns one version and causes no additional PUT
- Duplicate content across paths and versions creates one Blob/object
- A fault-injecting transfer port interrupts after one completed file; rerun uses real PostgreSQL and
  MinIO state and does not retransmit it
- PUT success followed by lost acknowledgement is recovered by deterministic-key verification
- Wrong, truncated, or externally corrupted object bytes are detected
- Verification failure repairs the same version and only failed/unverified Blobs
- Empty source becomes `READY` and pulls to an empty directory
- Pulled path set and every byte, size, and digest match the source
- API and CLI errors are actionable and contain no sensitive material

The interruption adapter is injected through an application transfer port. M1 adds no production
failure environment variable or hidden test endpoint. Integration tests never fall back to SQLite
or an in-memory object store.

## 14. Demo and completion gate

`scripts/demo-v01.sh` will:

1. run `docker compose up -d --build --wait`;
2. create temporary deterministic robot-like nested files plus duplicate content;
3. push them under a reserved synthetic demo Dataset;
4. show status and canonical manifest;
5. pull `v1` into a fresh destination;
6. compare recursive bytes and independently generated SHA-256 inventories;
7. remove temporary local files while preserving Compose volumes.

The demo contains only synthetic data and is repeatable: the same fixed content resolves the same
version and exercises idempotent skip behavior when rerun.

Completion requires fresh success from:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy robolake apps
uv run pytest
scripts/demo-v01.sh
```

The full pytest run includes live PostgreSQL and MinIO integration tests without skips. Required
M0 commands continue to work.

## 15. Documentation and decision records

Implementation updates README quickstart, API/CLI documentation, architecture, threat model, user
stories, and roadmap to reflect the re-baselined M1/M2 boundary.

- ADR 0003 is amended to state explicitly that an `AVAILABLE` physical Blob is immutable and that
  logical entries reference it. A duplicate ADR for content addressing is not created.
- ADR 0006 records the complete M1 single-PUT vertical slice, pre-registration size rejection,
  file-level resume, and multipart deferral to M2.
- ADR 0007 records that manifest identity is immutable in all states while `FAILED` is a recoverable
  publication workflow state.

The design does not cite future Iceberg integration or GC as reasons for M1. Content/logical
separation does not preclude later decisions, but those technologies and lifecycle policies remain
explicitly out of scope.

## 16. Acceptance and known limits

M1 is accepted when every required CLI command, test case, static check, migration, and demo succeeds
from the documented local environment. `push` either reaches `READY` or returns an actionable error;
unsupported oversized files create no PostgreSQL or object-storage state.

File-level resume means an interrupted single PUT retransmits that one file unless the provider
already committed the complete object and API reconciliation verifies it. M1 cannot resume a byte
range inside an incomplete large file. Files above `5_000_000_000` bytes require M2 multipart.

Empty child directories are not represented. Pull refuses an existing output path. Sequential
transfer favors simple, testable semantics over throughput. Continuous corruption detection,
post-publication repair, cleanup of killed local staging directories, deletion, GC, and production
authentication remain outside M1.
