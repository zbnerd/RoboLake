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
idempotency, storage control operations, and verification-policy enforcement. The Typer CLI scans local
files and transfers bytes directly to MinIO through short-lived presigned URLs. File bodies never
pass through the API, and the CLI never receives permanent object-storage credentials.

## 2. Goals and non-goals

### Goals

- Provide `push`, `status`, `manifest`, and `pull` as a usable vertical slice.
- Treat a canonical manifest as the immutable identity of a dataset version.
- Deduplicate physical bytes by SHA-256 while preserving logical paths in PostgreSQL.
- Resume an interrupted push without retransmitting blobs already verified or recoverable from the
  deterministic object key.
- Publish only after every referenced Blob has passed the M1 verification contract.
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
- Windows runtime, Windows filename semantics, SMB edge cases, and power-loss durability

## 3. Public CLI contract

```bash
robolake push ./examples/demo-dataset --dataset demo/pick-place
robolake status demo/pick-place@v1
robolake manifest demo/pick-place@v1
robolake pull demo/pick-place@v1 --output /tmp/robolake-restored
```

`push` scans, registers, transfers, verifies, and finalizes synchronously. Success means the command
returns only after the resolved version is `READY`. It reports logical snapshot totals, unique
content totals, invocation-scoped created/reused Blob totals, the version reference, and final state.
Progress is written to stderr; the final stdout line is
stable as `READY <dataset>@v<number>` so the demo can capture the resolved reference without parsing
decorative progress output.

`status` exposes two derived views. `snapshot` reports logical files/bytes and ready logical
files/bytes; `content` reports unique Blobs/bytes and available unique Blobs/bytes. Duplicate
logical entries that reference one Blob count separately only in snapshot totals. It also reports
derived `is_empty`, safe failure information, and a derived `next_action`.

`manifest` writes the stored canonical JSON bytes directly to stdout. `pull` requires an output path
that does not yet exist and reports materialized file and byte progress.

Dataset references split on the final `@v<positive integer>`. Dataset names are Unicode NFC text,
may contain `/` namespace separators, and reject control characters, `@`, leading or trailing `/`,
empty segments, and `.` or `..` segments. Names are metadata only and never enter filesystem paths
or object keys. Names remain case-sensitive after NFC normalization.

CLI configuration uses `ROBOLAKE_API_URL`. Public commands have useful `--help`; M1 does not add
`ls`, delete, GC, repair, auth, or multipart commands.

### M1 protocol constants and operational defaults

Protocol/schema constants affect validation or interoperability and are identical in every
deployment. They are named code constants, not environment variables; changing a manifest-validity
constant requires an explicit protocol/schema revision and ADR.

| Protocol constant | M1 value |
| --- | ---: |
| Dataset name | 255 UTF-8 bytes |
| Normalized path segment | 255 UTF-8 bytes |
| Normalized relative path | 1,024 UTF-8 bytes |
| Manifest entries | 100,000 |
| Canonical manifest | 67,108,864 bytes (64 MiB) |
| Failure code/detail | 64 ASCII characters / 512 UTF-8 bytes |

Operational settings do not alter manifest identity. Startup validation rejects values outside the
applicable protocol or provider hard maximum.

| Operational control | M1 default |
| --- | ---: |
| Per-file single PUT limit | 5,000,000,000 bytes; configurable downward only |
| Hash and transfer chunk | 1,048,576 bytes (1 MiB) |
| Presigned URL lifetime | 900 seconds |
| Concurrent file transfers per CLI | 1 |

Tests may lower operational size/TTL settings to exercise boundaries without large fixtures.

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
| `DatasetVersion` | UUID resource identity, dataset-scoped registration number, canonical manifest identity, immutable logical/unique totals, and publication workflow. Manifest and entries are immutable immediately after registration. |
| `DatasetEntry` | One immutable manifest ordinal and safe relative POSIX path referencing one Blob. |
| `Blob` | Physical content identity and object lifecycle. UUID is the relational key; SHA-256 is unique content identity; size disagreement for one digest is an integrity conflict. |
| `UploadSession` | One persisted single-PUT attempt for a missing Blob, including state, receipt, and safe failure information. |
| `IdempotencyRecord` | Operation scope, caller key, canonical request fingerprint, and stable resource/response. |

`Blob` stores `id`, `sha256`, `size_bytes`, `object_key`, state, verification timestamps, creation
timestamp, and bounded failure fields. Suffix-derived media/format hints are non-canonical,
non-authoritative presentation metadata and are not accepted or persisted in M1.

The object key is derived only from a lowercase digest:

```text
blobs/sha256/<first-2>/<next-2>/<64-char-sha256>
```

An `AVAILABLE` physical Blob is an immutable verification attestation. RoboLake never issues another
write URL for it. Later versions reuse that attestation without another storage read. M1 does
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
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

Canonicalization rules are contractual:

1. Accept Unicode scalar text only, normalize paths to NFC, and store POSIX `/` separators.
2. Reject absolute, drive-qualified, UNC, backslash-containing, control-character-containing,
   empty, `.`, and `..` segments; reject a segment over 255 UTF-8 bytes or a complete path over
   1,024 UTF-8 bytes.
3. Reject exact, NFC-normalized, case-folded, and file/ancestor-prefix collisions. The conservative
   collision model makes snapshots reconstructable across supported case-sensitive and
   case-insensitive Linux/macOS filesystems.
4. Sort entries by UTF-8 bytes of `relative_path`; this order defines immutable zero-based
   `manifest_ordinal` values in PostgreSQL.
5. Emit `relative_path`, `size_bytes`, and `sha256` in that order. M1 accepts no media type,
   format hint, filesystem metadata, or directory entry in canonical input.
6. Serialize UTF-8 with no BOM, indentation, insignificant whitespace, ASCII escaping, or trailing
   newline.
7. Hash exactly those canonical bytes with SHA-256.

The empty manifest is exactly:

```json
{"schema_version":1,"entries":[]}
```

The manifest is a regular-file tree snapshot. Its identity is schema version plus normalized
relative file paths, sizes, and SHA-256 byte identities. Parent directories are implicit; nested
empty directories, ownership, modes, timestamps, extended attributes, hard links, symlinks, and
special files are not represented. An empty root remains the canonical empty manifest above.

The scanner rejects a symlink root, symlinked directory or file, and every non-regular file. On
supported Linux/macOS runtimes it opens the root once and walks with directory descriptors and
dirfd-relative no-follow opens, so a discovered component cannot be swapped to an outside symlink
and followed. It validates every relative path and compares device, inode, size, and nanosecond
mtime before and after hashing. The upload path reopens the captured root/components under the same
contract and rechecks their identities. Hashing uses bounded chunks; memory use does not depend on
file size.

For a newly created object, the upload stream counts and hashes bytes again before accepting a 200
result. A mutated source therefore cannot create an `AVAILABLE` Blob for the sealed digest. A
provider may reject a create-only 412/409 before consuming the whole body, and an already AVAILABLE
Blob needs no local retransmission; those paths rely on server reconciliation and do not claim a
wire-byte count. Absolute source paths, timestamps, owners, and permissions never enter the
manifest or API payload.

M1 officially supports and contract-tests local filesystem behavior on Linux and macOS. Windows
runtime, Windows reserved-name semantics, and SMB-specific behavior are not claimed. Logical paths
and all error output escape untrusted control text before terminal or log rendering.

## 7. PostgreSQL schema and enforcement

### `datasets`

- `id uuid primary key`
- `name text not null unique` with non-empty and bounded-length checks
- `created_at timestamptz not null`

### `dataset_versions`

- UUID primary key and dataset foreign key
- positive `version_number`
- schema version, canonical `manifest_bytes bytea`, and lowercase 64-hex `manifest_sha256`
- state and nonnegative immutable `file_count`, `logical_bytes`, `unique_blob_count`, and
  `unique_blob_bytes`
- bounded `failure_code` and `failure_detail`
- creation, sealing, verification, and ready timestamps
- unique `(dataset_id, version_number)` and `(dataset_id, manifest_sha256)`

Canonical bytes use `bytea`, not JSONB, because JSONB does not preserve canonical serialization.
All four totals are recomputed from the canonical manifest during registration/finalization and
never serve as mutable progress counters. `is_empty` is never persisted; API and CLI derive it from
`file_count == 0`.

### `blobs`

- UUID primary key
- unique lowercase 64-hex SHA-256
- nonnegative size and unique deterministic object key
- constrained state and verification/failure timestamps

A duplicate SHA-256 with a different size is rejected as an integrity conflict rather than inserted
as another row.

### `dataset_entries`

- dataset-version and Blob foreign keys
- zero-based immutable `manifest_ordinal` and validated relative path
- primary key `(dataset_version_id, relative_path)`
- unique `(dataset_version_id, manifest_ordinal)`
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

Mutable completed counters are not stored. Snapshot readiness and content availability are derived
by joining immutable entries to Blob state, so interruption cannot cause counter drift.

Database constraints validate states, hashes, counts, sizes, keys, ordinals, and foreign references. Triggers
reject entry mutation or manifest-identity changes after sealing, illegal version/blob/session state
transitions, every semantic update to `READY`, and identity mutation of `AVAILABLE` Blobs. These are
last-line controls in addition to domain checks.

Dataset creation/find and its idempotency response commit in one transaction. Version registration
then locks that existing Dataset row and writes the version, entries, Blob upserts, sealing
timestamp, and registration idempotency response in a second transaction. A Dataset with no version
is a valid registry resource; the oversized-file preflight still runs before either transaction.
Each Blob upload/verification commits its own progress. Finalization locks the version and rechecks
logical and unique totals, contiguous ordinals, canonical manifest hash, and Blob availability in
one transaction.

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
```

An empty version follows `DRAFT -> VERIFYING -> READY`; verification checks empty entries, all four
zero totals, and the canonical empty-manifest hash. `READY` is terminal.

The manifest and entries are immutable in every state. `FAILED` describes publication workflow,
not mutable content identity. It means the same manifest can still reach `READY`; it does not mean
rerunning `push` alone can always repair the cause. A poisoned existing content key requires an
operator to inspect and remove the object before the same version can follow `FAILED -> UPLOADING ->
VERIFYING -> READY`. M1 provides no repair/delete command or admin API.

`STORED_OBJECT_MISMATCH` moves an M1 Blob and active version to `FAILED` and derives
`CONTACT_OPERATOR`. Any other non-READY Version referencing that Blob derives the same blocking
failure/action in status without duplicating a mutable progress or action column. A
provider-rejected checksum, a `409` with no visible object, network/database
failure, or temporary object-storage error leaves current progress state because its outcome is
retryable or ambiguous.
Invalid manifests and unsupported sizes are rejected before version creation. Network, database,
and temporary object-storage errors cannot be reliably persisted as a terminal workflow result.
Stable symbolic error/failure codes drive derived retry advice; separate
`FAILED_RECOVERABLE` and `FAILED_FATAL` states are not introduced.

## 9. Push and file-level resume

`push` executes these steps:

1. Validate and scan the complete source tree.
2. Produce canonical manifest bytes and digest.
3. Apply the M1 file-size preflight before any persistent operation.
4. Create/find the Dataset and register/find the manifest version idempotently.
5. Move a version with missing Blobs to `UPLOADING`.
6. For each unique manifest Blob, skip `AVAILABLE` or create/find its UploadSession.
7. Request a 900-second URL signed for the exact bucket, deterministic key, PUT method,
   `Content-Length`, `If-None-Match: *`, and `x-amz-checksum-sha256`. The checksum value is the
   Base64 form of the manifest's raw 32-byte digest. Omitting or changing a signed header invalidates
   the request.
8. Stream the local file directly to MinIO while recounting and rehashing it.
9. Interpret only provider status: `200` means created, `412` means reconcile a create-only race,
   and `409` means reconcile once and otherwise return a retryable conflict. The CLI never parses or
   exposes a provider error body.
10. Complete/reconcile without requiring ETag. The API performs `HeadObject` with checksum mode,
    requires exact size and provider system `ChecksumSHA256`, and falls back to a streamed full GET
    only when that system checksum is absent. User metadata and ETag are never integrity evidence.
11. Mark the session complete and Blob `AVAILABLE` only after that uniform contract succeeds.
    A matching pre-existing object is adopted; a mismatching object is never overwritten or
    automatically deleted.
12. Lock and finalize the version through `VERIFYING` to `READY` after every referenced Blob is
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

### Create-only concurrency, duplicate push, and interruption

DatasetVersion has an internal UUID; manifest SHA-256 is a content fingerprint, not a primary key.
`(dataset_id, manifest_sha256)` is the dataset-scoped snapshot identity and `<dataset>@vN` is its
human reference. Version numbers are manifest registration order, not READY order. Two datasets may
have distinct Version UUIDs/lineage for the same manifest while sharing all physical Blobs.

A repeated unchanged push resolves the same version; changed content creates the next registration
number even while an older version is non-READY. The older immutable version remains resumable if
its original manifest bytes become available again. CLI idempotency keys use
`robolake-m1:<operation>:<sha256-of-length-prefixed-canonical-fields>`, while the API stores the
canonical request fingerprint. Same key and same payload replay the original response; same key and
different payload returns conflict. The scopes are `dataset-create`, `version-register`, and
`upload-session-create`; completion and finalization are resource-idempotent.

After interruption, the CLI rescans and resolves the resulting manifest identity. For each
non-available Blob, the API inspects the deterministic key. If a PUT succeeded before the client
acknowledgement was persisted, the verification contract adopts it without retransmission. A
missing object receives a fresh create-only URL. A mismatching existing object produces
`STORED_OBJECT_MISMATCH`/`CONTACT_OPERATOR`; repeated push never overwrites or deletes it. After an
operator resolves the external condition, rerunning push repairs the same immutable version.

For concurrent create-only PUTs, `412` is not success: it requires API reconciliation and succeeds
only for a matching object. A `409` with a matching object also converges; a `409` with no visible
object returns `UPLOAD_CONFLICT`/`RETRY_PUSH` and leaves Blob, session, and Version non-terminal.
Every path converges on at most one active session, one Blob row, and one object.

Two logical paths or versions with the same digest and size reference one Blob and one physical
object. A duplicate push of a `READY` version performs no storage write after resolving and checking
the same manifest.

Push/resume progress uses unique content (`resolved_blob_count/unique_blob_count` and
`resolved_blob_bytes/unique_blob_bytes`). Invocation results distinguish `created_blob_*` from
`reused_blob_*`; they do not claim exact network bytes. On successful push, created plus reused
counts/bytes equal the unique totals. Exact wire/retry bytes are future telemetry, not M1 product
semantics.

## 10. Pull and reconstruction safety

The API issues download capabilities only for `READY` versions. The CLI fetches the sealed canonical
manifest, validates the entire path set again, and rejects an existing output path before creating
anything.

The CLI creates a private, uniquely named staging directory beside the intended output, ensuring the
same filesystem for publication. Before requesting a capability it prepares the safe parent and
exclusive temporary file. The M1 API then returns exactly one DatasetEntry and one presigned GET.
The CLI immediately streams that object while computing size and SHA-256, closes it, and atomically
renames a match to its logical staging path. Only then may it request the next capability.

The stateless cursor is canonical unpadded base64url over a fixed-width binary payload: one cursor
format-version byte, the 16-byte DatasetVersion UUID, and an unsigned 8-byte big-endian
`manifest_ordinal`. `null` selects ordinal zero. Strict decoding rejects non-canonical encodings,
unsupported formats, wrong Version UUIDs, and out-of-range ordinals. The cursor is protocol-bound,
not cryptographically bound or an authorization capability. Replaying it selects the same logical
entry and may return a newly signed URL. Reads never consume server state; the last entry returns a
null next cursor, while an empty READY version returns no capability and `complete=true`.

After every manifest entry succeeds, the CLI verifies the complete materialized path/count/byte set
and publishes with atomic no-replace semantics. Linux uses `renameat2(RENAME_NOREPLACE)` and macOS
uses `renamex_np(RENAME_EXCL)` through a small platform adapter. If the destination appeared, is a
symlink, or the platform/filesystem lacks no-replace support, publication fails closed with
`ATOMIC_PUBLISH_UNSUPPORTED` or `OUTPUT_EXISTS`; it never falls back to replacing rename.

An empty version publishes an empty staging directory without requesting a URL. A handled failure removes temporary
state best-effort and never creates the final output. An uncatchable process kill may leave only a
hidden staging directory, never files presented at the requested output path.

Download revalidates containment and refuses symlink parents, normalized collisions, and existing
targets. It never silently overwrites. Duplicate logical entries may download the same Blob more
than once in M1; using hard links would change file semantics, and a local download cache is outside
the required slice.

M1 guarantees atomic visibility for handled failures and normal process interruption, not
power-loss durability. It does not claim that file-only `fsync` makes a whole tree durable across a
kernel panic, filesystem failure, or sudden power loss; that guarantee requires a later filesystem
ADR and fault tests.

`READY` means every referenced Blob reached `AVAILABLE` through the verification contract before
publication. It does not mean every Blob was reread for this Version or continuously scrubbed. If
an operator mutates an object later, pull detects the checksum/size mismatch, removes staging, and
returns an integrity error. M1 does not regress the terminal version or add persistent
`CORRUPTED`; continuous auditing and repair require a later observed need.

## 11. API proposal

All endpoints live under `/v1` and exchange metadata/control JSON only.

| Method and path | Purpose |
| --- | --- |
| `POST /v1/datasets` | Idempotently create/find a normalized Dataset. |
| `POST /v1/datasets/{dataset_id}/versions` | Validate canonical manifest and register/find a sealed version. |
| `GET /v1/versions/resolve?dataset=...&version=...` | Resolve a CLI dataset reference without placing `/`-containing names in a path segment. |
| `GET /v1/versions/{version_id}` | Return state, snapshot/content totals, derived progress/`is_empty`, failure, and recovery advice. |
| `GET /v1/versions/{version_id}/manifest` | Return exact canonical bytes as `application/json`. |
| `POST /v1/versions/{version_id}/upload-sessions` | Get/create a session for one referenced Blob, or report it available. |
| `GET /v1/upload-sessions/{session_id}` | Return persisted/reconciled file-level state. |
| `POST /v1/upload-sessions/{session_id}/url` | Return a short-lived presigned PUT and required headers. |
| `POST /v1/upload-sessions/{session_id}/complete` | Reconcile provider-attested checksum/size with full-GET fallback; ETag optional. |
| `POST /v1/versions/{version_id}/finalize` | Verify aggregate invariants and publish idempotently. |
| `POST /v1/versions/{version_id}/download-plan` | Return at most one ordinal-bound entry and presigned GET capability. |

Mutating create/register calls require `Idempotency-Key`; resource-targeted completion and
finalization calls are naturally idempotent. The API derives object keys from validated digests and
never accepts a client-supplied physical key.

The API owns an internal S3 client for control/verification plus a presigning client configured with
`ROBOLAKE_S3_PUBLIC_ENDPOINT_URL`. PUT and GET capabilities expire after 900 seconds. Upload URLs
sign create-only, length, and checksum headers. Download-plan accepts only an optional cursor in its
POST body; no M1 limit parameter can request multiple capabilities. URLs and query strings never
enter logs, errors, database fields, CLI arguments, or telemetry.

## 12. Error and security contract

API failures use a stable envelope:

```json
{
  "error": {
    "code": "STORED_OBJECT_MISMATCH",
    "message": "Blob 8f2a... does not match its content address; contact the storage operator.",
    "next_action": "CONTACT_OPERATOR"
  }
}
```

Stable symbolic `code` values describe causes; `next_action` is derived from current state and cause
and is not a database column. CLI exit codes remain broad automation categories. Messages identify
a safely escaped logical relative path, version reference, or short digest prefix and give a
recovery action when one exists. They do not reveal an absolute source path, credential, presigned
URL/query, provider exception, database connection string, or dataset bytes. Free-text fields are
bounded and transport input is validated before application use.

HTTP mappings include 404 for missing references; 409 for idempotency/content conflicts, illegal
transitions, `VERSION_IMMUTABLE`, `STORED_OBJECT_MISMATCH`, and retryable `UPLOAD_CONFLICT`; 413 for
bounded request/manifest limits; 422 for invalid domain input/cursors; and 502/503 for safely
translated dependency failures. The symbolic code, not HTTP status alone, selects CLI action/exit.

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
- Empty and zero-byte files, Unicode NFC paths, nested paths, and large chunked reads
- Symlinks, special files, absolute/drive/UNC paths, separators, controls/surrogates, dot segments,
  segment/path limits, exact/NFC/case-folded ancestor collisions, and source mutation
- Dataset/version/blob/session state-transition tables and failure-code retry policy
- Empty-version invariants, duplicate manifest identity, object-key derivation, and size conflicts
- Logical/unique/invocation metric invariants, including duplicate-path Blob reuse and empty totals
- Fixed-width cursor canonicalization, Version binding, ordinal replay, and malformed input
- Pull preflight, containment, one-capability sequencing, existing-output refusal, temporary-file
  cleanup, and atomic no-replace publication
- Stable API exception mapping, CLI exit codes, useful messages, and secret/URL/path redaction
- Architecture import-boundary enforcement

### PostgreSQL integration tests

- Alembic upgrade from an empty database and downgrade
- Unique/check/foreign-key constraints and state/immutability triggers
- Concurrent version allocation and duplicate registration/idempotency races
- Atomic registration rollback and idempotent finalization
- Contiguous immutable manifest ordinals, immutable summary totals, and derived dual-view progress
- `READY` and sealed-entry mutation rejection

### MinIO and end-to-end integration tests

- Presigned create-only PUT signs length/checksum/precondition; correct create is 200, duplicate is
  rejected without mutation, checksum mismatch is rejected without an object, and concurrent
  creates store exactly one object
- Provider system checksum/size verification, missing-checksum full-GET fallback, and matching
  existing-object adoption
- `200=create`, `412=reconcile`, and `409=reconcile-or-retry` converge without overwrite/delete
- Duplicate push returns one version and causes no additional PUT
- Duplicate content across paths and versions creates one Blob/object
- A fault-injecting transfer port interrupts after one completed file; rerun uses real PostgreSQL and
  MinIO state and does not retransmit it
- PUT success followed by lost acknowledgement is recovered by deterministic-key reconciliation
- A poisoned existing key is detected, never overwritten/deleted, repeatedly reports
  `CONTACT_OPERATOR`, and the same Version resumes only after explicit test-operator cleanup
- A stale presigned PUT cannot overwrite an `AVAILABLE` object
- One-at-a-time GET capability issuance, cursor replay, fresh TTL after a slow prior file, GET begun
  before expiry completing after expiry, and a new GET after expiry failing against pinned MinIO
- Empty source becomes `READY` and pulls to an empty directory
- Pulled path set and every byte, size, and digest match the source
- Concurrent pulls to one output publish exactly one tree without replacing the winner
- API and CLI errors are actionable and contain no sensitive material

The interruption adapter is injected through an application transfer port. M1 adds no production
failure environment variable or hidden test endpoint. Integration tests never fall back to SQLite
or an in-memory object store.

## 14. Demo and completion gate

`scripts/demo-v01.sh` will:

1. run `docker compose up -d --build --wait`;
2. create temporary deterministic robot-like nested files plus duplicate content;
3. push them under a reserved synthetic demo Dataset and capture the stable final reference;
4. show status and canonical manifest for that reference;
5. pull the resolved version into a fresh destination;
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

- ADR 0002 records resource/content/reference identity layers, registration-order version numbers,
  and immutable non-READY snapshots.
- ADR 0003 records create-only physical keys, provider-attested verification with full-GET fallback,
  reusable `AVAILABLE` attestations, and canonical manifest minimalism. A duplicate content-address
  ADR is not created.
- ADR 0006 records the complete M1 single-PUT vertical slice, pre-registration size rejection,
  conditional-write convergence, one-capability pull, supported filesystems, and multipart deferral.
- ADR 0007 records immutable manifest identity, recoverable publication state, symbolic errors,
  and the detect-report-stop manual-repair boundary.

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

Empty child directories and filesystem metadata are not represented. M1 supports Linux/macOS local
filesystem contracts only. Pull refuses an existing output path, issues one GET capability at a
time, and guarantees atomic visibility rather than power-loss durability. Sequential transfer
favors simple semantics over throughput. Pull resume, continuous corruption detection,
post-publication repair, cleanup of killed local staging directories, deletion, GC, and production
authentication remain outside M1.
