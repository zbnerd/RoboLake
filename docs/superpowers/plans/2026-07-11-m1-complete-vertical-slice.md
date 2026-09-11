# RoboLake M1 Complete Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `push`, `status`, `manifest`, and `pull` as one tested vertical slice that safely
publishes immutable, content-addressed dataset versions and reconstructs them byte-for-byte.

**Architecture:** Keep the FastAPI service as the metadata, lifecycle, idempotency, presigning, and
verification control plane. Keep local scanning and direct presigned PUT/GET byte transfer in the
Typer CLI. PostgreSQL stores logical registry and file-level progress; MinIO stores immutable
SHA-256-addressed blobs. M1 uses one single PUT per unique Blob and resumes at file granularity.

**Tech Stack:** Python 3.12, FastAPI, Typer, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 17,
psycopg 3, boto3, MinIO/S3, HTTPX, pytest, Ruff, mypy, uv, Docker Compose.

## Global Constraints

- Read `AGENTS.md` and
  `docs/superpowers/specs/2026-07-11-m1-vertical-slice-design.md` before each task.
- Use complete type annotations and domain-specific exceptions.
- `robolake/domain` and `robolake/application` must not import FastAPI, Typer, SQLAlchemy, boto3,
  botocore, or MinIO libraries.
- The API never receives dataset file bodies. The CLI never receives permanent MinIO credentials.
- Canonical entries contain only `relative_path`, `size_bytes`, and `sha256`. Paths are Unicode
  scalar/NFC logical POSIX paths with fixed 255-byte segment and 1,024-byte total limits; exact,
  NFC, case-folded, and ancestor collisions fail. Absolute roots never leave CLI memory.
- Canonical manifest bytes, entries, and content identity are immutable immediately after
  registration. `READY` is terminal; `FAILED` is a recoverable publication state.
- `is_empty` is derived from `file_count == 0` and is never a database column.
- Physical keys use `blobs/sha256/<first-2>/<next-2>/<digest>`; every PUT is create-only and
  `AVAILABLE` is an immutable reusable verification attestation.
- M1 rejects a file larger than `5_000_000_000` bytes before any persistent mutation. Multipart and
  within-file resume remain M2.
- Fixed protocol constants: dataset name 255 UTF-8 bytes, segment 255 UTF-8 bytes, path 1,024 UTF-8
  bytes, 100,000 entries, and 64 MiB canonical manifest. Operational defaults: 1 MiB chunks,
  900-second URLs, and one sequential transfer. M1 download-plan emits at most one capability.
- M1 filesystem behavior is contract-tested on Linux/macOS. Pull uses atomic no-replace publication
  and promises atomic visibility, not Windows/SMB compatibility or power-loss durability.
- Use only synthetic test/demo bytes. Never log credentials, provider exception text, complete
  presigned URLs/query strings, or absolute source paths.
- Do not add auth, deletion, GC, refcount, workers, Kafka, Airflow, Iceberg, Parquet, ROS2, MCAP
  parsing, conversion, frontend, Kubernetes, or training behavior.
- Follow red-green-refactor. Do not weaken, skip, xfail, or hide integration failures.
- Do not create implementation commits until the user explicitly authorizes them. Every task ends
  with a diff review checkpoint instead of a commit.

---

## File map

### Domain

- `robolake/domain/errors.py`: stable domain error codes and safe context.
- `robolake/domain/identifiers.py`: dataset names/references, SHA-256, and relative-path values.
- `robolake/domain/manifest.py`: minimal entries, canonical serialization, digest, and totals.
- `robolake/domain/lifecycle.py`: version/Blob/session states and legal transitions.
- `robolake/domain/records.py`: framework-free Dataset, Version, Blob, session, and status records.

### Application

- `robolake/application/contracts.py`: typed request/result DTOs shared by services and adapters.
- `robolake/application/ports.py`: registry, object-store, control-plane, scanner, and byte-transfer
  protocols.
- `robolake/application/idempotency.py`: deterministic fingerprints and caller keys.
- `robolake/application/registry.py`: dataset/version create, resolve, status, and manifest use cases.
- `robolake/application/transfers.py`: create-only reconciliation, verification, finalization,
  cursor codec, and one-capability download-plan use cases.
- `robolake/application/push.py`: local end-to-end push orchestration and progress events.
- `robolake/application/pull.py`: READY-only, one-capability-at-a-time reconstruction.

### Infrastructure

- `robolake/infrastructure/scanner.py`: non-following recursive scan and streamed hashing.
- `robolake/infrastructure/models.py`: SQLAlchemy 2 mappings.
- `robolake/infrastructure/store.py`: PostgreSQL registry/progress adapter and transactions.
- `robolake/infrastructure/object_storage.py`: internal S3 control plus public-endpoint presigning.
- `robolake/infrastructure/api_client.py`: safe HTTP implementation of the CLI control-plane port.
- `robolake/infrastructure/http_transfer.py`: streamed PUT/GET with checksum and URL redaction.
- `robolake/infrastructure/atomic_tree.py`: private staging tree and atomic final placement.
- `robolake/infrastructure/atomic_publish.py`: Linux/macOS no-replace directory publication adapter.
- `robolake/infrastructure/settings.py`: exact M1 limits and API/storage endpoints.

### API and CLI

- `apps/api/dependencies.py`: typed app-state service accessors.
- `apps/api/errors.py`: exception handlers and stable error envelope.
- `apps/api/middleware/body_limit.py`: 64 MiB ASGI request limiter.
- `apps/api/schemas/datasets.py`, `apps/api/schemas/transfers.py`, `apps/api/schemas/errors.py`:
  transport-only Pydantic models.
- `apps/api/routes/datasets.py`, `apps/api/routes/versions.py`, `apps/api/routes/transfers.py`:
  `/v1` control endpoints.
- `apps/api/main.py`, `apps/api/router.py`: resource composition and routing.
- `apps/cli/commands/datasets.py`: four public M1 commands.
- `apps/cli/output.py`: stderr progress, stable stdout summaries, and exit-code mapping.
- `apps/cli/main.py`: CLI composition without business logic.

### Persistence, tests, demo, and docs

- `migrations/versions/20260711_0002_m1_registry.py`: M1 schema, constraints, indexes, and triggers.
- `tests/unit/domain/`, `tests/unit/application/`, `tests/unit/infrastructure/`: isolated contracts.
- `tests/unit/apps/api/`, `tests/unit/apps/cli/`: transport and actionable-error contracts.
- `tests/integration/conftest.py`: real PostgreSQL/MinIO isolation and cleanup.
- `tests/integration/test_migrations.py`, `test_registry.py`, `test_transfer_flow.py`,
  `test_cli_workflow.py`: database, storage, resume, corruption, and E2E evidence.
- `scripts/demo-v01.sh`: repeatable synthetic push/manifest/pull/compare demonstration.
- `.github/workflows/filesystem-contract.yml`: Ubuntu/macOS filesystem contract matrix.
- `.env.example`, `pyproject.toml`, `uv.lock`, `README.md`, `Makefile`, and approved docs/ADRs:
  configuration, operator workflow, and re-baselined decisions.

## Spec coverage map

| Requirement | Implemented and proven in |
| --- | --- |
| Safe recursive scan, POSIX normalization, streamed SHA-256 | Tasks 1–2 |
| Deterministic canonical manifest and empty manifest | Tasks 1–2 |
| Pre-persistence single-PUT limit rejection | Tasks 2, 8, 11 |
| Idempotent Dataset/version registration | Tasks 4–5, 7, 11 |
| Direct presigned MinIO upload and persisted file progress | Tasks 4, 6–8, 11 |
| Verified Blob deduplication | Tasks 5–6, 8, 11 |
| Explicit transitions and READY/AVAILABLE immutability | Tasks 3–7, 11 |
| Interrupted push and lost-acknowledgement recovery | Tasks 6, 8, 11 |
| Provider checksum/fallback verification and manual-repair boundary | Tasks 5–8, 11 |
| Status and exact stored manifest commands | Tasks 5, 7, 9, 11 |
| One-capability cursor pull, checksum validation, atomic no-replace tree publication | Tasks 4, 6–7, 10–11 |
| Logical/unique/invocation metrics without counter drift | Tasks 3–5, 8–11 |
| Actionable/redacted API and CLI errors | Tasks 1, 7–11 |
| Real PostgreSQL migration/constraint evidence | Tasks 4–5, 11 |
| Real MinIO and byte-identical end-to-end evidence | Tasks 6, 11 |
| Repeatable synthetic demo and updated decision docs | Task 12 |
| Full lint, format, type, tests, demo, and scope audit | Task 13 |

### Task 1: Canonical identifiers and manifest domain

**Files:**
- Create: `robolake/domain/constants.py`
- Create: `robolake/domain/errors.py`
- Create: `robolake/domain/identifiers.py`
- Create: `robolake/domain/manifest.py`
- Modify: `robolake/domain/__init__.py`
- Create: `tests/unit/domain/test_identifiers.py`
- Create: `tests/unit/domain/test_manifest.py`

**Interfaces:**
- Produces: `DatasetName.parse(raw: str) -> DatasetName`
- Produces: `DatasetReference.parse(raw: str) -> DatasetReference`
- Produces: `RelativePath.parse(raw: str) -> RelativePath`
- Produces: `Sha256Digest.parse(raw: str) -> Sha256Digest`
- Produces: `object_key_for(digest: Sha256Digest) -> str`
- Produces: `ManifestEntry`, `Manifest.build(entries)`, `Manifest.from_canonical_bytes(data)`
- Produces: fixed manifest-validity constants and `Manifest.canonical_bytes`, `sha256`, `file_count`,
  `logical_bytes`, `unique_blob_count`, and `unique_blob_bytes`

- [ ] **Step 1: Write identifier validation tests**

Create table-driven tests with these exact valid/invalid expectations:

```python
import pytest

from robolake.domain.errors import InvalidDatasetName, InvalidDatasetReference, UnsafePathError
from robolake.domain.identifiers import DatasetName, DatasetReference, RelativePath


@pytest.mark.parametrize("raw", ["demo/pick-place", "로봇/session-01", "A/b_c-1.2"])
def test_dataset_name_accepts_normalized_names(raw: str) -> None:
    assert DatasetName.parse(raw).value == raw


@pytest.mark.parametrize("raw", ["", "/demo", "demo/", "demo//x", "demo/../x", "a@v1"])
def test_dataset_name_rejects_ambiguous_names(raw: str) -> None:
    with pytest.raises(InvalidDatasetName):
        DatasetName.parse(raw)


def test_dataset_reference_splits_on_final_version_suffix() -> None:
    reference = DatasetReference.parse("demo/pick-place@v12")
    assert reference.dataset == DatasetName.parse("demo/pick-place")
    assert reference.version_number == 12


@pytest.mark.parametrize(
    "raw",
    [
        "/etc/passwd", "../escape", "a/../b", "C:/robot/data", "//server/share",
        "a\\b", "a\0b", "a\nb", "a/" + "x" * 256,
    ],
)
def test_relative_path_rejects_unsafe_forms(raw: str) -> None:
    with pytest.raises(UnsafePathError):
        RelativePath.parse(raw)
```

- [ ] **Step 2: Run identifier RED**

Run:

```bash
uv run pytest tests/unit/domain/test_identifiers.py -q
```

Expected: FAIL with `ModuleNotFoundError: robolake.domain.errors`.

- [ ] **Step 3: Implement errors and identifier values**

Use frozen, slotted dataclasses. Normalize names and paths with `unicodedata.normalize("NFC", raw)`.
Count UTF-8 bytes, not Python characters. Validate raw path separators before constructing any
`PurePosixPath` so `//` and dot segments are not silently collapsed.

The concrete public shapes are:

```python
@dataclass(frozen=True, slots=True)
class DatasetName:
    value: str

    @classmethod
    def parse(cls, raw: str) -> "DatasetName":
        normalized = unicodedata.normalize("NFC", raw)
        segments = normalized.split("/")
        invalid = (
            not normalized
            or len(normalized.encode("utf-8")) > 255
            or normalized.startswith("/")
            or normalized.endswith("/")
            or "@" in normalized
            or any(not segment or segment in {".", ".."} for segment in segments)
            or any(unicodedata.category(char) == "Cc" for char in normalized)
        )
        if invalid:
            raise InvalidDatasetName("Dataset name must use safe, non-empty namespace segments.")
        return cls(normalized)


@dataclass(frozen=True, slots=True)
class DatasetReference:
    dataset: DatasetName
    version_number: int

    @classmethod
    def parse(cls, raw: str) -> "DatasetReference":
        name, marker, number = raw.rpartition("@v")
        if not marker or not number.isdecimal() or int(number) < 1:
            raise InvalidDatasetReference("Expected DATASET@v<positive integer>.")
        return cls(DatasetName.parse(name), int(number))
```

Define `MAX_DATASET_NAME_BYTES=255`, `MAX_PATH_SEGMENT_BYTES=255`,
`MAX_RELATIVE_PATH_BYTES=1024`, `MAX_MANIFEST_ENTRIES=100_000`, and
`MAX_MANIFEST_BYTES=67_108_864` in `domain/constants.py`; they are not settings. `RelativePath`
rejects leading `/`, backslash, control/surrogate code points, drive/UNC forms, empty/dot segments,
segments over 255 UTF-8 bytes, and values over 1,024 UTF-8 bytes. Catch Unicode encoding failure and
raise `UnsafePathError`, never raw `UnicodeEncodeError`.
`Sha256Digest.parse` accepts lowercase 64-hex only and exposes `raw_bytes` and `checksum_base64`.
Every error class has a stable class-level `code` and a safe message; it stores only an optional
logical `relative_path`, never an absolute path.

Define this complete M1 hierarchy in `errors.py`: `RoboLakeError`; validation subclasses
`InvalidDatasetName`, `InvalidDatasetReference`, `InvalidDigestError`, `UnsafePathError`,
`PathCollisionError`, `UnsafeFileTypeError`, `SourceChangedError`, `UnsupportedFileSizeError`,
`UnsupportedPlatformError`, `ManifestMismatchError`, and `InvalidCursorError`; lookup/conflict
subclasses `NotFoundError`, `IdempotencyConflictError`, `ContentConflictError`,
`IllegalTransitionError`, `ImmutableVersionError`, `UploadConflictError`, and
`StoredObjectMismatchError`; local-output subclasses `OutputExistsError` and
`AtomicPublishUnsupportedError`; boundary subclasses `ApiProtocolError`,
`RetryableDependencyError`, `RetryableTransferError`, and `UploadChecksumRejectedError`. Each class
has an explicit stable symbolic `code`; do not derive wire contracts mechanically from class names.

Put validation/normalization in each value object's `__post_init__`; `parse` delegates to the normal
constructor. This prevents direct construction from bypassing invariants. Use
`object.__setattr__(self, "value", normalized)` only to store the NFC value in the frozen instance.
`object_key_for` returns
`f"blobs/sha256/{digest.value[:2]}/{digest.value[2:4]}/{digest.value}"`; no API schema accepts an
object key from a caller.

- [ ] **Step 4: Run identifier GREEN**

Run: `uv run pytest tests/unit/domain/test_identifiers.py -q`

Expected: all identifier cases pass.

- [ ] **Step 5: Write manifest golden and collision tests**

```python
from robolake.domain.errors import PathCollisionError
from robolake.domain.identifiers import RelativePath, Sha256Digest
from robolake.domain.manifest import Manifest, ManifestEntry

EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
A_SHA = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"


def test_manifest_has_golden_canonical_bytes_and_hash() -> None:
    manifest = Manifest.build(
        [
            ManifestEntry(RelativePath.parse("z.bin"), 0, Sha256Digest.parse(EMPTY_SHA)),
            ManifestEntry(RelativePath.parse("a.txt"), 1, Sha256Digest.parse(A_SHA)),
        ]
    )
    expected = (
        b'{"schema_version":1,"entries":['
        b'{"relative_path":"a.txt","size_bytes":1,'
        b'"sha256":"ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"},'
        b'{"relative_path":"z.bin","size_bytes":0,'
        b'"sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]}'
    )
    assert manifest.canonical_bytes == expected
    assert manifest.sha256.value == "b4d7ea7eafa613e1bb473a87a3e9f4e9656235982f67a09cae158b683a6c52a3"


def test_empty_manifest_is_canonical() -> None:
    manifest = Manifest.build([])
    assert manifest.canonical_bytes == b'{"schema_version":1,"entries":[]}'
    assert manifest.sha256.value == "ba4fc47c6525d8de1a187fe7d41e1de1b2fe52df1f6a43d0544675d3ac44e5fe"
    assert manifest.file_count == 0
    assert manifest.logical_bytes == 0
    assert manifest.unique_blob_count == 0
    assert manifest.unique_blob_bytes == 0


def test_manifest_rejects_casefold_collision() -> None:
    first = ManifestEntry(RelativePath.parse("Camera/A.bin"), 0, Sha256Digest.parse(EMPTY_SHA))
    second = ManifestEntry(RelativePath.parse("camera/a.bin"), 0, Sha256Digest.parse(EMPTY_SHA))
    with pytest.raises(PathCollisionError):
        Manifest.build([first, second])


def test_manifest_rejects_casefolded_file_ancestor_collision() -> None:
    first = ManifestEntry(RelativePath.parse("Foo"), 0, Sha256Digest.parse(EMPTY_SHA))
    second = ManifestEntry(RelativePath.parse("foo/bar.bin"), 0, Sha256Digest.parse(EMPTY_SHA))
    with pytest.raises(PathCollisionError):
        Manifest.build([first, second])
```

- [ ] **Step 6: Run manifest RED**

Run: `uv run pytest tests/unit/domain/test_manifest.py -q`

Expected: FAIL because `Manifest` and `ManifestEntry` are absent.

- [ ] **Step 7: Implement canonical manifest**

`Manifest.build` sorts with `entry.relative_path.value.encode("utf-8")`, rejects exact/NFC/casefold
duplicate and ancestor-prefix collisions, validates nonnegative sizes and same-digest/same-size,
enforces fixed entry/byte limits, computes logical/unique totals, and stores a tuple. Serialize with
`json.dumps(payload, ensure_ascii=False, separators=(",", ":"))` and encode once. Parse stored/API
bytes with strict UTF-8 JSON, reject unknown top-level/entry fields, rebuild the Manifest, and require
input bytes to equal rebuilt canonical bytes.

The only entry keys are `relative_path`, `size_bytes`, and `sha256`; reject `media_type`,
`media_type_hint`, directory entries, and every unknown field. M1 performs no suffix classification
or content sniffing.

- [ ] **Step 8: Run domain GREEN and review**

Run:

```bash
uv run pytest tests/unit/domain/test_identifiers.py tests/unit/domain/test_manifest.py -q
uv run ruff check robolake/domain tests/unit/domain
uv run mypy robolake/domain
git diff --check
```

Expected: all tests/static checks pass. Review only Task 1 paths with `git diff -- <paths>`; do not
commit.

### Task 2: Secure filesystem scanner and M1 preflight

**Files:**
- Create: `robolake/infrastructure/scanner.py`
- Create: `robolake/application/contracts.py`
- Modify: `robolake/infrastructure/settings.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/unit/infrastructure/test_scanner.py`
- Modify: `tests/unit/infrastructure/test_settings.py`

**Interfaces:**
- Consumes: `RelativePath`, `Sha256Digest`, `ManifestEntry`, `Manifest`
- Produces: `FileIdentity`, `LocalFileRef`, and `ScannedFile(entry, local_ref)`; absolute root fields
  use `repr=False` and are never serialized
- Produces: `ScannedDataset(root: Path, manifest: Manifest, files: Sequence[ScannedFile])`
- Produces: `FileSystemScanner.scan(source: Path) -> ScannedDataset`

- [ ] **Step 1: Write settings and scanner RED tests**

Add settings assertions for `api_url`, operational M1 limits, and 900-second TTL; assert startup
rejects a single-PUT value above 5,000,000,000. Scanner tests must create
two trees in reverse creation order and assert equal canonical bytes/digest; scan an empty directory;
reject root/file/directory symlinks and a FIFO; lower `max_single_put_bytes` to reject a named file;
ignore nested empty directories; reject control/surrogate/overlong segments; and monkeypatch
`os.fstat` to return a changed final `st_mtime_ns`. Add a race test that swaps a child directory for
a symlink to an outside tree between discovery and open; the scan must fail without reading the
outside file. Test an extracted
`iter_file_chunks(stream, chunk_size)` helper with a guarded synthetic stream whose `read` raises
unless called with exactly 1,048,576, proving no unbounded read path exists.

The essential assertions are:

```python
first = FileSystemScanner().scan(first_root)
second = FileSystemScanner().scan(second_root)
assert first.manifest.canonical_bytes == second.manifest.canonical_bytes
assert first.manifest.sha256 == second.manifest.sha256
assert all(item.local_ref.root.is_absolute() for item in first.files)
assert str(first_root.resolve()).encode() not in first.manifest.canonical_bytes

empty = FileSystemScanner().scan(empty_root)
assert empty.manifest.file_count == 0

with pytest.raises(UnsupportedFileSizeError, match="large.bin"):
    FileSystemScanner(max_single_put_bytes=7).scan(root_with_eight_byte_file)
```

- [ ] **Step 2: Run scanner RED**

Run:

```bash
uv run pytest tests/unit/infrastructure/test_settings.py \
  tests/unit/infrastructure/test_scanner.py -q
```

Expected: FAIL because the scanner and M1 settings do not exist.

- [ ] **Step 3: Add exact settings and runtime HTTPX**

Move `httpx>=0.28,<1` from the dev group to runtime dependencies. Add frozen settings with these
fields/defaults:

```python
api_url: str = "http://localhost:18000"
stream_chunk_bytes: int = 1_048_576
max_single_put_bytes: int = 5_000_000_000
presigned_url_ttl_seconds: int = 900
```

Only these operational values receive `ROBOLAKE_` environment settings. Import fixed manifest
constants from `robolake.domain.constants`; do not mirror them as settings. Validate positive chunk
and TTL values and `max_single_put_bytes <= 5_000_000_000`. Run `uv lock`, then `uv sync`.

- [ ] **Step 4: Implement descriptor-relative non-following scan**

Use `source.lstat()` to reject a symlink/non-directory root, then open it with
`O_RDONLY|O_DIRECTORY|O_NOFOLLOW` and require the opened `(st_dev, st_ino)` to match the `lstat`
identity. Recurse using directory file
descriptors (`os.scandir(dir_fd)` plus `os.open(name, ..., dir_fd=dir_fd)`), not joined absolute
paths. Open child directories with `O_DIRECTORY|O_NOFOLLOW`; open regular files with `O_NOFOLLOW`.
Validate each opened descriptor with `fstat`, and reject every symlink or other mode rather than
skipping it. A discovery/open race may produce a safe error but must never follow the replacement.

For a regular file:

```python
flags = os.O_RDONLY | os.O_NOFOLLOW
descriptor = os.open(raw_name, flags, dir_fd=parent_fd)
try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise UnsafeFileTypeError(relative_path.value)
    if before.st_size > self._max_single_put_bytes:
        raise UnsupportedFileSizeError(relative_path.value, before.st_size)
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb", closefd=False) as stream:
        for chunk in iter(lambda: stream.read(self._chunk_size), b""):
            digest.update(chunk)
    after = os.fstat(descriptor)
finally:
    os.close(descriptor)
```

Require `(st_dev, st_ino, st_size, st_mtime_ns)` to remain equal. Build logical paths from the raw
relative component tuple and pass the POSIX join to `RelativePath.parse`; do not derive containment
from a later path resolution. Collect at most 100,000 entries and build `Manifest`.

Store the selected root, its device/inode identity, raw relative components, and scanned file
identity only in a local `LocalFileRef`; its absolute root is `repr=False` and never enters a
manifest/API/error. Task 8 reopens that root and every component descriptor-relatively with
`O_NOFOLLOW`, verifies the captured identities, then streams. At CLI startup, fail with
`UNSUPPORTED_PLATFORM` unless `sys.platform` is Linux or macOS or the required dirfd/no-follow
capabilities are unavailable. Directory entries and filesystem metadata never enter
`ManifestEntry`.

- [ ] **Step 5: Run scanner GREEN and architecture checks**

Run:

```bash
uv run pytest tests/unit/infrastructure/test_settings.py \
  tests/unit/infrastructure/test_scanner.py -q
uv run ruff check robolake/application/contracts.py robolake/infrastructure/scanner.py \
  tests/unit/infrastructure/test_scanner.py
uv run mypy robolake/application/contracts.py robolake/infrastructure/scanner.py
git diff --check
```

Expected: all scanner/settings tests and checks pass. Confirm test fixtures contain only synthetic
bytes and no manifest includes an absolute path. Review Task 2 paths; do not commit.

### Task 3: Lifecycle states and framework-free records

**Files:**
- Create: `robolake/domain/lifecycle.py`
- Create: `robolake/domain/records.py`
- Create: `tests/unit/domain/test_lifecycle.py`
- Create: `tests/unit/domain/test_records.py`

**Interfaces:**
- Produces: `VersionState`, `BlobState`, `UploadSessionState`, `FailureCode`, `NextAction`
- Produces: `require_version_transition`, `require_blob_transition`, `require_session_transition`
- Produces: `DatasetRecord`, `VersionRecord`, `BlobRecord`, `UploadSessionRecord`, `VersionStatus`

- [ ] **Step 1: Write transition-table RED tests**

Use exhaustive parameter tables. Required allowed edges are:

```python
VERSION_EDGES = {
    (VersionState.DRAFT, VersionState.UPLOADING),
    (VersionState.DRAFT, VersionState.VERIFYING),
    (VersionState.UPLOADING, VersionState.VERIFYING),
    (VersionState.VERIFYING, VersionState.READY),
    (VersionState.VERIFYING, VersionState.FAILED),
    (VersionState.FAILED, VersionState.UPLOADING),
}
BLOB_EDGES = {
    (BlobState.PENDING, BlobState.UPLOADING),
    (BlobState.UPLOADING, BlobState.VERIFYING),
    (BlobState.VERIFYING, BlobState.AVAILABLE),
    (BlobState.VERIFYING, BlobState.FAILED),
    (BlobState.FAILED, BlobState.UPLOADING),
}
SESSION_EDGES = {
    (UploadSessionState.CREATED, UploadSessionState.IN_PROGRESS),
    (UploadSessionState.IN_PROGRESS, UploadSessionState.COMPLETED),
    (UploadSessionState.IN_PROGRESS, UploadSessionState.FAILED),
}
```

Same-state calls are idempotent no-ops. Every other edge raises `IllegalTransitionError`. Add a test
that no transition leaves `VersionState.READY` or `BlobState.AVAILABLE`.

- [ ] **Step 2: Run lifecycle RED**

Run: `uv run pytest tests/unit/domain/test_lifecycle.py tests/unit/domain/test_records.py -q`

Expected: FAIL because lifecycle types are absent.

- [ ] **Step 3: Implement enums, transitions, and records**

Use `enum.StrEnum`. Persisted M1 `FailureCode` contains only `STORED_OBJECT_MISMATCH`.
`NextAction` contains `NONE`, `RETRY_PUSH`, `RETRY_STATUS`, and `CONTACT_OPERATOR`; it is derived and
never persisted. The transition helper returns the target after validating equality or membership.

`VersionRecord` exposes derived values without persistence duplication:

```python
@dataclass(frozen=True, slots=True)
class VersionRecord:
    id: UUID
    dataset_id: UUID
    dataset_name: DatasetName
    version_number: int
    manifest_sha256: Sha256Digest
    state: VersionState
    file_count: int
    logical_bytes: int
    unique_blob_count: int
    unique_blob_bytes: int
    failure_code: FailureCode | None = None
    failure_detail: str | None = None

    @property
    def reference(self) -> DatasetReference:
        return DatasetReference(self.dataset_name, self.version_number)

    @property
    def is_empty(self) -> bool:
        return self.file_count == 0
```

`VersionStatus` contains nested immutable views:

```python
@dataclass(frozen=True, slots=True)
class SnapshotStatus:
    file_count: int
    logical_bytes: int
    ready_file_count: int
    ready_logical_bytes: int


@dataclass(frozen=True, slots=True)
class ContentStatus:
    unique_blob_count: int
    unique_blob_bytes: int
    available_blob_count: int
    available_blob_bytes: int
```

`VersionStatus` contains the `VersionRecord`, both views, a derived
`blocking_failure_code: FailureCode | None`, and derived `next_action`. The blocking failure is the
Version failure when present or the highest-priority failure on any referenced Blob; it is not a
new DatasetVersion column. This lets every non-READY Version sharing a poisoned Blob report
`CONTACT_OPERATOR`, even when another Version initiated the one active upload session.

Validate completed values against totals. READY empty versions expose 100% for both views; a
zero-denominator pre-READY percentage is `None`, never 0%. Derive `CONTACT_OPERATOR` only for
`STORED_OBJECT_MISMATCH`, `NONE` for READY, `RETRY_STATUS` for VERIFYING, and `RETRY_PUSH` for other
non-terminal states. One AVAILABLE Blob may satisfy multiple ready logical entries.

The other record shapes are fixed:

```python
@dataclass(frozen=True, slots=True)
class DatasetRecord:
    id: UUID
    name: DatasetName


@dataclass(frozen=True, slots=True)
class BlobRecord:
    id: UUID
    sha256: Sha256Digest
    size_bytes: int
    object_key: str
    state: BlobState
    failure_code: FailureCode | None = None


@dataclass(frozen=True, slots=True)
class UploadSessionRecord:
    id: UUID
    blob_id: UUID
    initiating_version_id: UUID
    state: UploadSessionState
    etag: str | None = None
```

- [ ] **Step 4: Run lifecycle GREEN and review**

Run:

```bash
uv run pytest tests/unit/domain/test_lifecycle.py tests/unit/domain/test_records.py -q
uv run ruff check robolake/domain tests/unit/domain
uv run mypy robolake/domain
git diff --check
```

Expected: all domain tests/static checks pass. Review Task 3 paths; do not commit.

### Task 4: PostgreSQL models, migration, constraints, and triggers

**Files:**
- Create: `robolake/infrastructure/models.py`
- Create: `migrations/versions/20260711_0002_m1_registry.py`
- Modify: `migrations/env.py`
- Modify: `tests/integration/test_dependencies.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_migrations.py`
- Create: `tests/integration/test_database_constraints.py`

**Interfaces:**
- Consumes: lifecycle enum string values and exact M1 length/size constraints
- Produces: `DatasetModel`, `DatasetVersionModel`, `BlobModel`, `DatasetEntryModel`,
  `UploadSessionModel`, `IdempotencyRecordModel`
- Produces: Alembic head `20260711_0002`

- [ ] **Step 1: Add isolated migration/database RED tests**

In `tests/integration/conftest.py`, define an `IntegrationSettings` Pydantic-settings class that
loads `.env.example`/`.env`, including root MinIO credentials. Create a unique temporary PostgreSQL
database from the configured maintenance connection, yield its URL, terminate remaining connections,
and drop it in `finally`. Do not downgrade or truncate the developer's normal database.

`test_migrations.py` runs:

```python
environment = {**os.environ, "ROBOLAKE_DATABASE_URL": temporary_database_url}
subprocess.run(
    ["uv", "run", "alembic", "upgrade", "head"],
    cwd=repository_root,
    env=environment,
    check=True,
    capture_output=True,
    text=True,
)
```

Then inspect all six table names and revision `20260711_0002`, downgrade to `20260710_0001`, assert
the six tables are gone, and upgrade back to head. Add constraint tests that attempt negative sizes,
invalid digest/state values, duplicate version/path/ordinal, noncontiguous registration ordinals,
sealed-entry or immutable-summary update, illegal state transition, and READY mutation using
SQLAlchemy Core; each must raise `IntegrityError`.

- [ ] **Step 2: Run migration RED**

Run:

```bash
docker compose up -d --wait
uv run pytest tests/integration/test_migrations.py \
  tests/integration/test_database_constraints.py -q
```

Expected: FAIL because revision `20260711_0002` and M1 tables do not exist.

- [ ] **Step 3: Add SQLAlchemy mappings**

Use SQLAlchemy 2 `Mapped[T]`/`mapped_column`, native PostgreSQL UUID, timezone-aware timestamps,
and relationships only where repository operations need them. Match these columns exactly:

```text
datasets(id, name, created_at)
dataset_versions(id, dataset_id, version_number, manifest_schema_version, manifest_bytes,
                 manifest_sha256, state, file_count, logical_bytes, unique_blob_count,
                 unique_blob_bytes, failure_code, failure_detail,
                 created_at, sealed_at, verifying_at, ready_at)
blobs(id, sha256, size_bytes, object_key, state, failure_code, failure_detail,
      created_at, verified_at)
dataset_entries(dataset_version_id, manifest_ordinal, relative_path, blob_id)
upload_sessions(id, blob_id, initiating_version_id, strategy, state, etag,
                failure_code, failure_detail, created_at, last_activity_at, completed_at)
idempotency_records(scope, key, request_sha256, resource_type, resource_id,
                    http_status, response_json, created_at)
```

Use an application-generated UUID default and UTC-aware timestamp default. `manifest_bytes` is
`LargeBinary`; `response_json` is PostgreSQL JSONB. Import `robolake.infrastructure.models` from
`migrations/env.py` after `Base` exists so autogeneration metadata is complete.

- [ ] **Step 4: Implement migration DDL and trigger guards**

Create tables in foreign-key order and drop them in reverse order. Add named checks for lower 64-hex
digests, nonnegative counts/sizes, exact state sets, bounded text fields, schema version 1, and
`strategy = 'SINGLE_PUT'`. Add unique constraints/indexes from the spec, including a partial unique
index on active sessions where state is `CREATED` or `IN_PROGRESS`.

Create PostgreSQL trigger functions with explicit allowed pairs. The version update guard must use
the equivalent of:

```sql
IF OLD.sealed_at IS NOT NULL AND (
  NEW.dataset_id IS DISTINCT FROM OLD.dataset_id OR
  NEW.version_number IS DISTINCT FROM OLD.version_number OR
  NEW.manifest_schema_version IS DISTINCT FROM OLD.manifest_schema_version OR
  NEW.manifest_bytes IS DISTINCT FROM OLD.manifest_bytes OR
  NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256 OR
  NEW.file_count IS DISTINCT FROM OLD.file_count OR
  NEW.logical_bytes IS DISTINCT FROM OLD.logical_bytes OR
  NEW.unique_blob_count IS DISTINCT FROM OLD.unique_blob_count OR
  NEW.unique_blob_bytes IS DISTINCT FROM OLD.unique_blob_bytes
) THEN
  RAISE EXCEPTION 'sealed dataset version content is immutable';
END IF;

IF OLD.state = 'READY' AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'READY dataset version is immutable';
END IF;
```

The same function rejects state changes outside Task 3's table. Add unique
`(dataset_version_id, manifest_ordinal)` and `(dataset_version_id, relative_path)` constraints. A
DatasetEntry INSERT/UPDATE/DELETE trigger queries its parent version and rejects mutation when
`sealed_at IS NOT NULL`; ordinal is immutable with the entry. Blob/session
guards enforce their Task 3 edges and reject digest/size/key mutation; an `AVAILABLE` Blob rejects
every semantic update. Keep trigger messages free of payload values.

- [ ] **Step 5: Apply migration and run GREEN**

Run:

```bash
uv run alembic upgrade head
uv run pytest tests/integration/test_migrations.py \
  tests/integration/test_database_constraints.py \
  tests/integration/test_dependencies.py -q
uv run ruff check robolake/infrastructure/models.py migrations tests/integration
uv run mypy robolake/infrastructure/models.py
git diff --check
```

Expected: migration round-trip and every constraint/trigger test pass; normal database reports head
`20260711_0002`. Review Task 4 paths; do not commit.

### Task 5: Idempotent registry application and SQL store

**Files:**
- Modify: `robolake/application/contracts.py`
- Create: `robolake/application/ports.py`
- Create: `robolake/application/idempotency.py`
- Create: `robolake/application/registry.py`
- Create: `robolake/infrastructure/store.py`
- Create: `tests/unit/application/test_idempotency.py`
- Create: `tests/unit/application/test_registry.py`
- Create: `tests/integration/test_registry.py`

**Interfaces:**
- Produces: `RegistryStore` protocol and `SqlAlchemyStore`
- Produces: `RegistryService.create_dataset`, `register_version`, `resolve`, `status`, `manifest`
- Produces: `UploadContext`, `UploadPreparation`, `PresignedRequest`, `DownloadItem`,
  `DownloadCapability`
- Produces: `make_idempotency_key(operation: str, fields: Sequence[bytes]) -> str` and
  `request_fingerprint(fields: Sequence[bytes]) -> Sha256Digest`

The read signatures are
`resolve(reference: DatasetReference) -> VersionRecord`,
`status(version_id: UUID) -> VersionStatus`, and
`manifest(version_id: UUID) -> Manifest`.

- [ ] **Step 1: Define contract tests with a fake store**

Create these immutable application DTOs before defining fakes:

```python
@dataclass(frozen=True, slots=True)
class UploadContext:
    version: VersionRecord
    blob: BlobRecord
    session: UploadSessionRecord | None


@dataclass(frozen=True, slots=True)
class UploadPreparation:
    version: VersionRecord
    blob_sha256: Sha256Digest
    size_bytes: int
    available: bool
    session_id: UUID | None


@dataclass(frozen=True, slots=True)
class PresignedRequest:
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class DownloadItem:
    entry: ManifestEntry
    request: PresignedRequest


@dataclass(frozen=True, slots=True)
class DownloadCapability:
    item: DownloadItem | None
    next_cursor: str | None
    complete: bool
```

Write an in-memory fake implementing this exact `RegistryStore` interface:

```python
class RegistryStore(Protocol):
    def create_dataset(
        self, name: DatasetName, idempotency_key: str, request_sha256: Sha256Digest
    ) -> DatasetRecord:
        raise NotImplementedError

    def register_version(
        self,
        dataset_id: UUID,
        manifest: Manifest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> VersionRecord:
        raise NotImplementedError

    def resolve_version(self, reference: DatasetReference) -> VersionRecord:
        raise NotImplementedError

    def get_manifest(self, version_id: UUID) -> Manifest:
        raise NotImplementedError

    def get_status(self, version_id: UUID) -> VersionStatus:
        raise NotImplementedError
    def prepare_upload(
        self,
        version_id: UUID,
        sha256: Sha256Digest,
        idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext:
        raise NotImplementedError

    def get_upload(self, session_id: UUID) -> UploadContext:
        raise NotImplementedError

    def mark_upload_in_progress(self, session_id: UUID) -> UploadContext:
        raise NotImplementedError

    def restart_failed_upload(
        self, version_id: UUID, blob_id: UUID, idempotency_key: str,
        request_sha256: Sha256Digest,
    ) -> UploadContext:
        raise NotImplementedError

    def mark_upload_verified(self, session_id: UUID, etag: str | None) -> UploadContext:
        raise NotImplementedError
    def mark_upload_failed(
        self, session_id: UUID, code: FailureCode, detail: str
    ) -> UploadContext:
        raise NotImplementedError

    def finalize_version(self, version_id: UUID) -> VersionRecord:
        raise NotImplementedError

    def get_download_entry(
        self, version_id: UUID, manifest_ordinal: int
    ) -> ManifestEntry | None:
        raise NotImplementedError
```
The concrete `SqlAlchemyStore` supplies every body. Tests require normalized dataset creation, server
canonicalization, duplicate same-manifest resolution to the same `v1`, changed manifest to `v2`
while `v1` remains non-READY, empty registration, exact manifest bytes/ordinals/summaries, dual-view
derived status, and safe not-found errors.

- [ ] **Step 2: Run registry unit RED**

Run:

```bash
uv run pytest tests/unit/application/test_idempotency.py \
  tests/unit/application/test_registry.py -q
```

Expected: FAIL because application contracts/services do not exist.

- [ ] **Step 3: Implement deterministic idempotency and registry service**

Encode each field as `8-byte big-endian length || UTF-8/value bytes`, hash the concatenation, and
return `robolake-m1:<operation>:<hex>`. Supported operations are exactly `dataset-create`,
`version-register`, and `upload-session-create`. `request_fingerprint` returns the same digest
without the textual prefix.

`RegistryService` is framework-free and delegates atomic persistence to the port:

```python
class RegistryService:
    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    def create_dataset(self, name: DatasetName, idempotency_key: str) -> DatasetRecord:
        fingerprint = request_fingerprint([name.value.encode("utf-8")])
        return self._store.create_dataset(name, idempotency_key, fingerprint)

    def register_version(
        self, dataset_id: UUID, manifest: Manifest, idempotency_key: str
    ) -> VersionRecord:
        fingerprint = request_fingerprint(
            [dataset_id.bytes, manifest.sha256.raw_bytes, manifest.canonical_bytes]
        )
        return self._store.register_version(dataset_id, manifest, idempotency_key, fingerprint)
```

`resolve`, `status`, and `manifest` resolve/read through the store. Application code re-parses
canonical bytes before returning them, so corrupt registry bytes become `ManifestMismatchError`.

- [ ] **Step 4: Write real store integration RED tests**

Against a migrated temporary PostgreSQL database from Task 4, require:

- same name/key/payload returns one Dataset;
- same key with a different name raises `IdempotencyConflictError`;
- two concurrent registrations of one manifest return the same `v1` UUID;
- two different concurrent manifests receive unique `v1` and `v2` numbers;
- duplicate SHA with different size raises `ContentConflictError` and rolls back all entries;
- status shows one AVAILABLE Blob satisfying multiple logical entries and immutable unique totals;
- ordinals are contiguous canonical UTF-8 path order and exact ordinal lookup ignores DB collation;
- direct SQL mutation of a registered entry and READY version fails.

- [ ] **Step 5: Run store RED**

Run: `uv run pytest tests/integration/test_registry.py -q`

Expected: FAIL because `SqlAlchemyStore` is absent.

- [ ] **Step 6: Implement SQL store transactions**

`create_dataset` first locks/replays `(scope, key)`, compares request digest, inserts with uniqueness
recovery, and stores a stable response in the same transaction. `register_version` locks the Dataset
row, checks `(dataset_id, manifest_sha256)` before allocating, uses `max(version_number) + 1` under
that lock, upserts each Blob by unique SHA, rejects size disagreement, inserts canonical
`manifest_ordinal` values, stores logical/unique summary totals, sets `sealed_at`, and persists the
idempotency response atomically.

`prepare_upload` validates digest membership. It returns immediately for `AVAILABLE`, reuses one
active session, creates a session for PENDING, or returns FAILED context without mutating it.
`restart_failed_upload` is allowed only after TransferService observes the deterministic key is
missing (operator removed it); it transitions the same Blob/Version to UPLOADING and creates one
session. The partial unique index is concurrency authority.

`mark_upload_verified` advances Blob `UPLOADING -> VERIFYING -> AVAILABLE` and session
`IN_PROGRESS -> COMPLETED` in one transaction. `mark_upload_failed` advances Blob
`UPLOADING -> VERIFYING -> FAILED`, session `IN_PROGRESS -> FAILED`, and active version
`UPLOADING -> VERIFYING -> FAILED` through separate guarded UPDATE statements inside one
transaction. This preserves the approved version state machine while recording immediate
per-Blob verification failure.

`get_status` performs aggregate SQL for snapshot/content views and referenced-Blob failures; no
mutable progress counter or duplicated next action is stored. `finalize_version` locks the Version, re-parses canonical bytes, recounts all four immutable
totals/contiguous ordinals, requires every Blob AVAILABLE, follows `DRAFT|UPLOADING -> VERIFYING ->
READY`, and replays READY. Empty versions validate the empty hash/totals and derive 100% only once
READY.

- [ ] **Step 7: Run registry GREEN and review**

Run:

```bash
uv run pytest tests/unit/application/test_idempotency.py \
  tests/unit/application/test_registry.py \
  tests/integration/test_registry.py -q
uv run ruff check robolake/application robolake/infrastructure/store.py \
  tests/unit/application tests/integration/test_registry.py
uv run mypy robolake/application robolake/infrastructure/store.py
git diff --check
```

Expected: all registry/idempotency/concurrency tests pass. Review Task 5 paths; do not commit.

### Task 6: S3 gateway, upload reconciliation, verification, and finalization

**Files:**
- Modify: `robolake/infrastructure/object_storage.py`
- Create: `robolake/application/transfers.py`
- Modify: `robolake/application/ports.py`
- Create: `tests/unit/application/test_transfers.py`
- Modify: `tests/unit/infrastructure/test_object_storage.py`
- Create: `tests/integration/test_presigned_transfer.py`

**Interfaces:**
- Produces: `ObjectStorePort.head`, `iter_bytes`, `presign_put`, `presign_get`
- Produces: `S3ObjectStore`
- Produces: `TransferService.prepare_upload`, `issue_upload_url`, `complete_upload`, `finalize`,
  `download_plan`
- Produces: `encode_cursor(version_id, manifest_ordinal)` and strict `decode_cursor`

- [ ] **Step 1: Write transfer-service RED tests**

Use these exact object-store signatures:

```text
head(object_key: str) -> ObjectInfo | None
iter_bytes(object_key: str, chunk_size: int) -> Iterator[bytes]
presign_put(object_key: str, size_bytes: int, checksum_base64: str,
            expires_seconds: int) -> PresignedRequest
presign_get(object_key: str, expires_seconds: int) -> PresignedRequest
```

Use fake RegistryStore/ObjectStore implementations. Require these cases:

```text
AVAILABLE Blob                         -> no URL and no object write
non-available Blob + matching HEAD system checksum -> mark AVAILABLE, no GET/URL
non-available Blob + no system checksum + matching GET -> mark AVAILABLE
non-available Blob + missing object   -> return session for PUT
FAILED Blob + missing object after operator cleanup -> restart same Blob/Version/session
non-available Blob + wrong object     -> FAILED + CONTACT_OPERATOR, never overwrite/delete
complete + matching object            -> session COMPLETED, Blob AVAILABLE
complete + missing object             -> UPLOAD_CONFLICT, state unchanged
finalize empty/all-available version  -> READY
download plan for non-READY            -> IllegalTransitionError
same cursor                            -> same ordinal/entry, fresh URL
wrong-Version/noncanonical/out-of-range cursor -> InvalidCursorError
```

- [ ] **Step 2: Run transfer RED**

Run: `uv run pytest tests/unit/application/test_transfers.py -q`

Expected: FAIL because `TransferService` and the object-store port are absent.

- [ ] **Step 3: Implement object verification and service**

Define immutable
`ObjectInfo(size_bytes: int, checksum_sha256: Sha256Digest | None, etag: str | None)` and
`PresignedRequest(url: str, headers: Mapping[str, str])` in contracts. Fields holding URLs/headers
use `repr=False`. `ObjectStorePort` accepts only server-derived keys. `head` requests checksum mode.
Verification rejects size mismatch first; exact provider system SHA-256 succeeds without GET;
absence streams 1 MiB chunks and independently hashes/counts; mismatch becomes one stable
`STORED_OBJECT_MISMATCH` cause.

The service algorithm is concrete:

```python
def complete_upload(self, session_id: UUID, etag: str | None = None) -> UploadPreparation:
    context = self._store.get_upload(session_id)
    try:
        self._verify_object(context.blob)
    except StoredObjectMismatchError:
        self._store.mark_upload_failed(
            session_id,
            FailureCode.STORED_OBJECT_MISMATCH,
            "Stored object does not match its content address.",
        )
        raise
    except NotFoundError as error:
        raise UploadConflictError("Upload outcome is not visible; rerun push.") from error
    verified = self._store.mark_upload_verified(session_id, etag)
    return UploadPreparation(
        version=verified.version,
        blob_sha256=verified.blob.sha256,
        size_bytes=verified.blob.size_bytes,
        available=True,
        session_id=verified.session.id if verified.session is not None else None,
    )
```

`prepare_upload` returns AVAILABLE immediately, otherwise reconciles the deterministic key using the
same helper. Matching existing content marks AVAILABLE without a URL. Mismatch marks
session/Blob/active Version FAILED and raises `StoredObjectMismatchError`; it never deletes or
presigns overwrite. Missing content returns/reuses an active session, or calls
`restart_failed_upload` after explicit operator cleanup. `issue_upload_url` marks the session in
progress and signs exactly one PUT for 900 seconds with `If-None-Match: *`, Content-Length, and
ChecksumSHA256.

Cursor payload is `struct.pack(">B16sQ", 1, version_id.bytes, ordinal)` encoded by canonical
unpadded `urlsafe_b64encode`; strict decode requires exact length, canonical re-encoding, format 1,
matching endpoint Version, and ordinal below `file_count`. `download_plan(version_id, cursor)` has no
limit parameter. It returns no item/`complete=True` for an empty READY Version, otherwise exact
`manifest_ordinal`, one fresh 900-second GET, next cursor for ordinal+1, and `complete` when current
is last. It queries by ordinal equality, never collation or OFFSET.

- [ ] **Step 4: Extend the S3 adapter and its unit tests**

Create separate internal and public-endpoint boto3 clients. `head` catches only Botocore
`ClientError` codes `404`, `NoSuchKey`, and `NotFound`; other provider failures propagate to the API
boundary. `head` sends `ChecksumMode="ENABLED"`, reads only the provider-owned
`ChecksumSHA256` field, and never treats user metadata or ETag as proof. `iter_bytes` closes
`StreamingBody` in `finally`. PUT presigning supplies bucket, key, `IfNoneMatch="*"`, exact
`ContentLength`, and Base64 `ChecksumSHA256`; all three required headers are in the signature. GET
presigning supplies only bucket/key and expiry.

Tests assert the public URL host, exact signed parameters and headers, checksum-mode HEAD, 404
handling, streaming close, omission of user metadata from verification, and that
`repr(PresignedRequest)` is never used in safe errors.

- [ ] **Step 5: Write and run real MinIO presign tests**

Run against the exact Compose image `minio/minio:RELEASE.2025-09-07T16-13-09Z`. Use unique
synthetic digests/keys and prove:

- the first conditional create returns 200 and exposes the matching provider checksum;
- a duplicate and a stale presigned request return 412 without changing stored bytes;
- simultaneous creates converge on exactly one stored object;
- a matching pre-existing object reconciles to AVAILABLE;
- a mismatching pre-existing object is reported and is never overwritten or deleted;
- wrong bytes under the signed expected checksum return 400 and create no object;
- missing provider checksum takes the streamed-GET fallback in an adapter/service test;
- a GET started before a short expiry may complete after expiry, while a new GET after expiry fails.

Exercise 409 deterministically at the adapter/service boundary: completion reconciles once, succeeds
if the matching object is visible, and otherwise returns retryable `UPLOAD_CONFLICT` without a
terminal state transition. Never treat ETag as SHA-256 and never parse provider error bodies in the
CLI-facing layer.

Run:

```bash
uv run pytest tests/unit/application/test_transfers.py \
  tests/unit/infrastructure/test_object_storage.py \
  tests/integration/test_presigned_transfer.py -q
uv run ruff check robolake/application/transfers.py \
  robolake/infrastructure/object_storage.py tests
uv run mypy robolake/application/transfers.py robolake/infrastructure/object_storage.py
git diff --check
```

Expected: all transfer and live MinIO tests pass. Review Task 6 paths; do not commit.

### Task 7: FastAPI control plane and safe error envelope

**Files:**
- Create: `apps/api/dependencies.py`
- Create: `apps/api/errors.py`
- Create: `apps/api/middleware/__init__.py`
- Create: `apps/api/middleware/body_limit.py`
- Create: `apps/api/schemas/errors.py`
- Create: `apps/api/schemas/datasets.py`
- Create: `apps/api/schemas/transfers.py`
- Create: `apps/api/routes/datasets.py`
- Create: `apps/api/routes/versions.py`
- Create: `apps/api/routes/transfers.py`
- Modify: `apps/api/router.py`
- Modify: `apps/api/main.py`
- Create: `tests/unit/apps/api/test_dataset_routes.py`
- Create: `tests/unit/apps/api/test_transfer_routes.py`
- Create: `tests/unit/apps/api/test_error_contract.py`
- Modify: `tests/unit/apps/api/test_system_routes.py`

**Interfaces:**
- Consumes: `RegistryService`, `TransferService`, domain records/errors
- Produces: all `/v1` endpoints in the approved spec
- Produces: `ApiServices(health, registry, transfers)` app-state container
- Produces: `build_api_services(settings: Settings) -> ApiServices`

`ApiServices` has exact fields `health: HealthService`, `registry: RegistryService`,
`transfers: TransferService`, and `close: Callable[[], None]`. The close callable owns engine/client
cleanup; injected unit-test containers supply a no-op callable.

- [ ] **Step 1: Write API RED tests**

Build `create_app(services=fake_services)` and require:

- create Dataset and register canonical manifest with required `Idempotency-Key`;
- resolve `demo/pick-place@v1` through query parameters;
- status exposes derived `is_empty`, logical snapshot progress, and unique-content progress, but
  schemas contain no persistence-facing `is_empty` or mutable progress input;
- manifest response body exactly equals canonical bytes and has `application/json` content type;
- upload preparation, URL, idempotent complete with optional ETag, finalize, and single-capability
  download-plan routes call the service with typed IDs/digests;
- download-plan accepts no `limit`, returns at most one URL, and rejects malformed,
  non-canonical, cross-Version, and out-of-range cursors;
- `application/octet-stream` file bodies sent to control endpoints are rejected before service
  invocation, proving no API route proxies dataset bytes;
- missing key/invalid manifest/actionable conflicts map to stable status/error codes;
- a 64 MiB + 1 byte body returns 413 without invoking a route;
- no response/error includes injected credentials, provider URLs, or exception text.

- [ ] **Step 2: Run API RED**

Run:

```bash
uv run pytest tests/unit/apps/api/test_dataset_routes.py \
  tests/unit/apps/api/test_transfer_routes.py \
  tests/unit/apps/api/test_error_contract.py -q
```

Expected: FAIL because M1 schemas/routes/handlers are absent.

- [ ] **Step 3: Implement transport schemas and middleware**

Pydantic request schemas use strict fields, exact maximum lengths/list sizes, and forbid extras.
Registration accepts schema version plus entries and rebuilds a domain Manifest; it never accepts
canonical bytes as trusted identity. Response schemas translate UUID/enums/digests to JSON and
derive `is_empty` from the record.

Implement pure ASGI body limiting by wrapping `receive`, counting every `http.request` body chunk,
and returning the stable 413 envelope once accumulated bytes exceed 67,108,864. Do not rely only on
`Content-Length`.

- [ ] **Step 4: Implement exception mapping and routes**

Use this exact envelope:

```python
class ErrorDetail(BaseModel):
    code: str
    message: str
    next_action: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
```

Map unsafe/input errors to 422, not-found to 404, idempotency/content/transition/immutability to
409, integrity mismatch to 409, translated object-store upstream failures to 502, and database/API
unavailability to 503. Derive `next_action` from the typed error/state at the response boundary; do
not persist it. At minimum, `STORED_OBJECT_MISMATCH` maps to `CONTACT_OPERATOR` and
`UPLOAD_CONFLICT` maps to `RETRY_PUSH`. Unexpected exceptions are caught only by the API boundary,
logged without request body/query secrets, and returned as code `INTERNAL_ERROR` with generic
detail. Log only the exception class name and a correlation identifier; do not log `str(error)`, a
raw traceback, request URL, or provider response.

Use `Header(alias="Idempotency-Key")` for create/register/session creation. Put `/v1` on one parent
router. Dataset names containing `/` are query values, never path captures.

- [ ] **Step 5: Compose owned resources and preserve health behavior**

`build_api_services(settings)` creates engine/session factory, internal and presigning S3 clients,
`SqlAlchemyStore`, RegistryService, TransferService, and HealthService. It also returns owned
closeables in the container. `create_app(services: ApiServices | None = None)` calls that factory only
when no container is injected. Lifespan closes both S3 clients and disposes the engine. Update system
route tests to inject a complete fake container; do not add globals or construct services in routes.

- [ ] **Step 6: Run API GREEN and review**

Run:

```bash
uv run pytest tests/unit/apps/api -q
uv run ruff check apps/api tests/unit/apps/api
uv run mypy apps/api
git diff --check
```

Expected: all API/system/error tests pass and no provider detail leaks. Review Task 7 paths; do not
commit.

### Task 8: Safe control-plane client, streamed PUT, and push workflow

**Files:**
- Create: `robolake/application/push.py`
- Modify: `robolake/application/contracts.py`
- Modify: `robolake/application/ports.py`
- Create: `robolake/infrastructure/api_client.py`
- Create: `robolake/infrastructure/http_transfer.py`
- Create: `tests/unit/application/test_push.py`
- Create: `tests/unit/infrastructure/test_api_client.py`
- Create: `tests/unit/infrastructure/test_http_transfer.py`

**Interfaces:**
- Produces: `ControlPlanePort`, `ByteTransferPort`, `ProgressEvent`, `PushResult`
- Produces: `RoboLakeApiClient`, `HttpByteTransfer`, `PushWorkflow.run`
- Consumes: `FileSystemScanner`, Registry/Transfer API schemas, presigned request headers

- [ ] **Step 1: Write push orchestration RED tests**

Define fakes for scanner, control plane, transfer, and progress sink. Require:

- preflight scanner error causes zero control-plane and transfer calls;
- duplicate paths sharing one digest cause one upload;
- an already-AVAILABLE preparation increments invocation-scoped reused Blob metrics and never
  requests a URL;
- a READY registration returns without preparing any upload and reports every unique Blob reused;
- a failed/recoverable version uploads only unavailable digests;
- 200, 412, and 409 PUT outcomes all call idempotent completion/reconciliation exactly once;
- a reconciled 412 counts as reused, a successful 200 counts as created, and an unresolved 409
  remains retryable without finalization;
- success calls finalize once and returns internally consistent logical, unique-content, created,
  and reused totals;
- a transfer exception propagates as retryable and does not call completion/finalization.

Use the exact public signatures
`PushWorkflow(scanner: ScannerPort, control_plane: ControlPlanePort, transfer: ByteTransferPort,
progress: Callable[[ProgressEvent], None])` and
`PushWorkflow.run(source: Path, dataset: DatasetName) -> PushResult`. Step 6 contains the complete
run algorithm; do not add a second public entry point.

- [ ] **Step 2: Run push RED**

Run: `uv run pytest tests/unit/application/test_push.py -q`

Expected: FAIL because `PushWorkflow` and client-side ports are absent.

- [ ] **Step 3: Define control-plane and byte-transfer contracts**

Add these exact operations to `ControlPlanePort`:

```text
create_dataset(name, idempotency_key) -> DatasetRecord
register_version(dataset_id, manifest, idempotency_key) -> VersionRecord
resolve(reference) -> VersionRecord
status(version_id) -> VersionStatus
manifest(version_id) -> Manifest
prepare_upload(version_id, digest, idempotency_key) -> UploadPreparation
upload_url(session_id) -> PresignedRequest
complete_upload(session_id, etag) -> UploadPreparation
finalize(version_id) -> VersionRecord
download_plan(version_id, cursor) -> DownloadCapability
```

`ByteTransferPort.put(local_ref, request, expected_entry) -> PutReceipt` securely reopens and
streams a request and
classifies only its HTTP status as CREATED (200), ALREADY_EXISTS (412), or CONFLICT (409). It never
parses a provider body. Task 10 adds `download_to(request, sink, expected_entry) -> TransferReceipt`
to the same port. Progress is based on resolved unique Blobs, not logical files or estimated wire
bytes, and never contains an absolute source path or URL.

Use these exact progress/result shapes:

```python
class ProgressKind(StrEnum):
    SCANNED = "SCANNED"
    UPLOADING = "UPLOADING"
    SKIPPED = "SKIPPED"
    VERIFIED = "VERIFIED"
    FINALIZING = "FINALIZING"
    DOWNLOADING = "DOWNLOADING"
    RESTORED = "RESTORED"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    kind: ProgressKind
    relative_path: RelativePath | None
    resolved_blob_count: int
    unique_blob_count: int
    resolved_blob_bytes: int
    unique_blob_bytes: int


@dataclass(frozen=True, slots=True)
class PushResult:
    reference: DatasetReference
    state: VersionState
    file_count: int
    logical_bytes: int
    unique_blob_count: int
    unique_blob_bytes: int
    created_blob_count: int
    created_blob_bytes: int
    reused_blob_count: int
    reused_blob_bytes: int
```

For every successful invocation, created plus reused counts/bytes equal the unique-content totals.
These are object-creation outcomes, not exact network telemetry; do not expose `uploaded_bytes`.

- [ ] **Step 4: Implement safe HTTP API client**

Use one `httpx.Client` with the configured base URL and bounded control-plane timeouts. Serialize
manifest entries from domain values. For every
non-2xx response, parse only the stable envelope; if
parsing fails, raise `ApiProtocolError("RoboLake API returned an invalid error response.")`. Map safe
codes and `next_action` to domain/application errors without concatenating `request.url`, response
headers, or provider bodies. The API client never sees or interprets S3/MinIO XML errors.

`manifest()` consumes raw response bytes and calls `Manifest.from_canonical_bytes`. Download/upload
capability URLs live only inside `PresignedRequest` and use `repr=False` fields.

- [ ] **Step 5: Implement hashing PUT stream**

`HttpByteTransfer.put` reopens the captured root, verifies its device/inode, walks every raw parent
component with dirfd-relative `O_DIRECTORY|O_NOFOLLOW`, opens the final file with `O_NOFOLLOW`, and
requires the scanned file identity before reading. It passes a generator to HTTPX that updates
SHA-256/count for each 1 MiB chunk. Copy only API-returned required headers and
set no caller-chosen storage key. For 200, require the entire expected body was consumed, its
size/digest matched, and device/inode/size/mtime remained unchanged. For 412/409, the provider may
reject before consuming the full body, so local counted bytes are not a product metric; API
completion proves the deterministic stored key instead.

```python
@dataclass(frozen=True, slots=True)
class PutReceipt:
    outcome: PutOutcome
    etag: str | None
```

Translate connection/timeouts and 408/429/5xx other than the explicit 409 contract to
`RetryableTransferError`. Return typed outcomes for 200/412/409. Translate every other status to
`TransferRejectedError` containing only status and relative path; never include or inspect its body.
Explicitly redact query strings from any HTTPX exception before safe logging. The direct-transfer HTTPX client uses
`httpx.Timeout(connect=30, read=300, write=None, pool=30)` so a started large-file body is not cut off
by a client write timer.

- [ ] **Step 6: Implement push algorithm**

Scan first, derive `dataset-create` and `version-register` keys, then register. Build an insertion-
ordered digest-to-first-source map from sorted manifest entries. For each unique digest call
`prepare_upload`; count AVAILABLE as reused; otherwise request URL, PUT, and call complete exactly
once for CREATED, ALREADY_EXISTS, or CONFLICT. Classify CREATED only when a 200 result completes;
classify reconciled 412/409 as reused. If 409 completion still finds no object, propagate retryable
`UPLOAD_CONFLICT` with state unchanged. Emit unique-Blob progress only after AVAILABLE. Re-read the
returned preparation rather than assuming completion. Finalize after all unique Blobs resolve.
Require returned version manifest hash to match the local Manifest on every registration/retry.

- [ ] **Step 7: Run push/client/HTTP GREEN and review**

Run:

```bash
uv run pytest tests/unit/application/test_push.py \
  tests/unit/infrastructure/test_api_client.py \
  tests/unit/infrastructure/test_http_transfer.py -q
uv run ruff check robolake/application/push.py robolake/infrastructure/api_client.py \
  robolake/infrastructure/http_transfer.py tests/unit
uv run mypy robolake/application/push.py robolake/infrastructure/api_client.py \
  robolake/infrastructure/http_transfer.py
git diff --check
```

Expected: all client/push/stream/redaction tests pass. Review Task 8 paths; do not commit.

### Task 9: Typer push, status, and canonical manifest commands

**Files:**
- Create: `apps/cli/commands/__init__.py`
- Create: `apps/cli/commands/datasets.py`
- Create: `apps/cli/output.py`
- Modify: `apps/cli/main.py`
- Modify: `tests/unit/apps/cli/test_cli.py`
- Create: `tests/unit/apps/cli/test_dataset_commands.py`

**Interfaces:**
- Produces: `CliServices(control_plane, push_workflow)` composition object
- Produces: `push`, `status`, `manifest` commands and stable exit mapping
- Consumes: `PushWorkflow`, `RoboLakeApiClient`, domain reference/status/manifest records

At this task boundary `CliServices` has exact fields `control_plane: ControlPlanePort`,
`push_workflow: PushWorkflow`, and `close: Callable[[], None]`. Task 10 adds the required
`pull_workflow: PullWorkflow` field without changing the other types.

- [ ] **Step 1: Write CLI RED tests**

Use Typer `CliRunner` with `ctx.obj`/app state containing fake services. Require root help to list
`push`, `status`, and `manifest`; push help to show `SOURCE` and required `--dataset`;
status/manifest to require
`DATASET@vN`; and exact outputs:

```text
stderr contains: Scanned 2 files (12 bytes)
stdout final:    READY demo/pick-place@v1\n
status contains: State: READY, Snapshot: 2/2 files (12/12 logical bytes), Content: 1/1 blobs (6/6 unique bytes), Empty: no
empty status:    State: READY, Snapshot: 0/0 files (0/0 logical bytes), Content: 0/0 blobs (0/0 unique bytes), Empty: yes
manifest stdout: exact canonical bytes with no added newline
```

Test exit codes 2/3/4/5 and assert an injected absolute path, credential, and presigned query never
appear in output.

- [ ] **Step 2: Run CLI RED**

Run: `uv run pytest tests/unit/apps/cli/test_cli.py tests/unit/apps/cli/test_dataset_commands.py -q`

Expected: FAIL because M1 commands are absent and the old test forbids push/pull.

- [ ] **Step 3: Split composition from commands**

Keep the root Typer object and existing `version`/`example` behavior. Add a production
`build_cli_services(settings: Settings) -> CliServices` factory that owns/cleans an HTTPX client and
constructs scanner, API client, HTTP transfer, and push workflow. This is normal
dependency injection, not a test-only hook. Commands obtain `CliServices` from `typer.Context.obj`;
the root callback creates it only when tests/callers have not supplied one and registers
`ctx.call_on_close(services.close)` for owned HTTP clients.

- [ ] **Step 4: Implement output and three commands**

Progress uses `typer.echo(rendered_progress, err=True)`. Push writes only `READY <reference>` to stdout after final
success. Its summary distinguishes logical snapshot totals, unique content, and created/reused Blob
outcomes; it never labels them wire-upload bytes. Status renders both logical snapshot and unique
content progress and includes safe failure/next-action lines only when populated. A READY empty
Version renders both progress views as complete; before READY, zero-denominator percentages are
omitted rather than shown as 0%. Manifest uses `sys.stdout.buffer.write(manifest.canonical_bytes)` and flushes; it
does not call `typer.echo`, transcode, or append a newline.

One exception-to-exit function maps validation/unsafe/size to 2, not-found/conflict/state to 3,
retryable dependency/transfer to 4, checksum/size integrity to 5, and safe unknown boundary errors
to 1. Catch only `RoboLakeError` plus one outer application-boundary `Exception` translation; never
print a traceback, URL, secret, or raw provider detail.

- [ ] **Step 5: Run CLI GREEN and help checks**

Run:

```bash
uv run pytest tests/unit/apps/cli/test_cli.py tests/unit/apps/cli/test_dataset_commands.py -q
uv run robolake --help
uv run robolake push --help
uv run robolake status --help
uv run robolake manifest --help
uv run ruff check apps/cli tests/unit/apps/cli
uv run mypy apps/cli
git diff --check
```

Expected: CLI tests/checks pass and help text is actionable. Review Task 9 paths; do not commit.

### Task 10: Safe atomic pull and pull CLI

**Files:**
- Create: `robolake/application/pull.py`
- Modify: `robolake/application/ports.py`
- Create: `robolake/infrastructure/atomic_tree.py`
- Create: `robolake/infrastructure/atomic_publish.py`
- Modify: `robolake/infrastructure/http_transfer.py`
- Modify: `apps/cli/commands/datasets.py`
- Modify: `apps/cli/main.py`
- Create: `.github/workflows/filesystem-contract.yml`
- Create: `tests/unit/application/test_pull.py`
- Create: `tests/unit/infrastructure/test_atomic_tree.py`
- Create: `tests/unit/infrastructure/test_atomic_publish.py`
- Modify: `tests/unit/apps/cli/test_dataset_commands.py`

**Interfaces:**
- Produces: `TreeDownloaderPort.begin`, `prepare`, `write`, `commit`, `abort`
- Produces: `AtomicTreeDownloader`
- Produces: `PullWorkflow.run(reference, output) -> PullResult`
- Modifies: `CliServices` and `build_cli_services` to include `pull_workflow`

The exact tree port is:

```text
begin(output: Path, manifest: Manifest) -> None
prepare(entry: ManifestEntry) -> PreparedDownload
write(prepared: PreparedDownload, item: DownloadItem) -> None
commit() -> Path
abort() -> None
```

`PreparedDownload` is an opaque local handle and never crosses the control-plane boundary.
`PullResult` is immutable and contains `reference`, final `output`, `materialized_file_count`, and
`materialized_bytes`.

- [ ] **Step 1: Write pull workflow and filesystem RED tests**

Application tests require non-READY refusal before `begin`, cursors `None -> token -> None`, exactly
one capability per request, local `prepare` before each capability request, cursor advancement only
after verified `write`, replay returning the same entry, commit after the final entry, abort on any
prepare/request/write error, and empty READY commit with no capability requests or writes.

Infrastructure tests require:

- existing output path refusal, even when it is an empty directory;
- symlinked output parent refusal;
- preflight rejection of unsafe/colliding manifest paths before staging creation;
- private sibling staging directory;
- exclusive temporary sibling per file and atomic rename after size/hash match;
- corrupt/truncated response removes temp/staging and leaves no final output;
- success reconstructs nested bytes and publishes the whole tree with a platform-specific atomic
  no-replace primitive;
- a race that creates the final output has exactly one winner and never replaces either tree;
- Linux uses `renameat2(RENAME_NOREPLACE)`, macOS uses `renamex_np(RENAME_EXCL)`, and an unsupported
  primitive fails closed;
- empty manifest creates an empty final directory.

- [ ] **Step 2: Run pull RED**

Run:

```bash
uv run pytest tests/unit/application/test_pull.py \
  tests/unit/infrastructure/test_atomic_tree.py -q
```

Expected: FAIL because pull workflow/tree downloader are absent.

- [ ] **Step 3: Implement one-capability-at-a-time PullWorkflow**

Resolve the reference, require READY, fetch and revalidate canonical manifest, then call
`tree.begin(output, manifest)`. For each canonical manifest ordinal, first call `tree.prepare(entry)`
to establish the safe parent and exclusive temporary file, then request one capability with the
current cursor. Require the returned entry to equal the expected ordinal, immediately stream it to
the prepared handle, verify/hash/place it, and only then adopt `next_cursor`. Never prefetch a second
URL. Catch `Exception` at this application workflow boundary, call `abort`, and re-raise; handle
`KeyboardInterrupt` in a separate clause that also aborts and re-raises. Do not catch unhandleable
process-kill signals. Commit once after a complete manifest comparison and return logical
materialization counts/output. M1 intentionally discards staging and restarts the whole pull after
failure; it has no Range retry or pull resume.

- [ ] **Step 4: Implement AtomicTreeDownloader**

Preflight all paths and output parents before creating a sibling named
`.robolake-<output-name>-<uuid>.tmp` with mode `0o700`. For each DownloadItem, create parent
directories without following symlinks, create `.filename.<uuid>.part` with
`O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` and mode `0o600`, and stream GET bytes through the HTTP transfer
into that descriptor while hashing/counting. Creating this descriptor is `prepare`; URL issuance
happens only afterward. On exact match, atomically rename the part within the private staging tree.
After every file succeeds, publish staging without replacement: call
`renameat2(RENAME_NOREPLACE)` on Linux or `renamex_np(RENAME_EXCL)` on macOS through a narrow tested
adapter. Never fall back to a replacing rename or a check-then-rename sequence. If the kernel or
filesystem cannot provide the primitive, return an actionable unsupported-filesystem error and
leave the requested output untouched.

`abort` recursively removes only the generated staging path after proving its resolved parent/name
match the recorded values. It never removes the requested output or follows symlinks. Provider/HTTP
errors mention only the logical path and status. M1 guarantees atomic visibility, not survival
across sudden power loss; it does not claim directory-fsync durability.

- [ ] **Step 5: Add pull command and run GREEN**

Add `robolake pull DATASET@vN --output PATH`, local output validation, stderr progress, and a final
stdout summary `RESTORED <reference> -> <user-supplied-path>`. The path may be shown locally but is
never sent to the API. Update the root-help assertion to require all four M1 commands after this
step. Add an Ubuntu/macOS CI matrix for domain/scanner/atomic-tree/atomic-publish contract tests;
full PostgreSQL/MinIO integration remains on Linux.

Run:

```bash
uv run pytest tests/unit/application/test_pull.py \
  tests/unit/infrastructure/test_atomic_tree.py \
  tests/unit/infrastructure/test_atomic_publish.py \
  tests/unit/apps/cli/test_dataset_commands.py -q
uv run robolake pull --help
uv run ruff check robolake/application/pull.py robolake/infrastructure/atomic_tree.py \
  robolake/infrastructure/atomic_publish.py \
  apps/cli tests/unit
uv run mypy robolake/application/pull.py robolake/infrastructure/atomic_tree.py \
  robolake/infrastructure/atomic_publish.py apps/cli
git diff --check
```

Expected: pull/filesystem/CLI tests pass with no partial final output. Review Task 10 paths; do not
commit.

### Task 11: Real PostgreSQL/MinIO/API/CLI vertical-slice evidence

**Files:**
- Modify: `tests/integration/conftest.py`
- Create: `tests/integration/test_transfer_flow.py`
- Create: `tests/integration/test_cli_workflow.py`
- Modify: `tests/integration/test_health.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: complete M1 API, workflows, SQL store, MinIO gateway, and installed CLI
- Produces: required interruption, idempotency, dedupe, corruption, empty, and byte-identity evidence

- [ ] **Step 1: Build deterministic live-service fixtures**

Create one migrated temporary PostgreSQL database for the M1 integration session and truncate only
that database's M1 tables in foreign-key order before each test. Create per-test synthetic Dataset
names with UUID suffixes. Record every test-created MinIO object key explicitly and delete only those
exact keys with root test credentials in cleanup; bytes incorporate a random test UUID so they cannot
reuse a pre-existing development Blob accidentally. Never truncate the normal database or run
`docker compose down -v`.

Construct `build_api_services(Settings(database_url=temporary_database_url))`, start the host-code
FastAPI app on an ephemeral localhost port in a daemon thread using `uvicorn.Server`, wait on
`/health`, yield the base URL, and set `server.should_exit = True` in cleanup. This avoids testing a
stale image while still using real PostgreSQL/MinIO. Presigned URLs continue to target
`localhost:19000`.

- [ ] **Step 2: Write required full-flow RED tests**

Add one focused test per required behavior:

```text
test_empty_directory_pushes_ready_and_pulls_empty
test_oversized_preflight_creates_no_database_or_object_state
test_duplicate_push_returns_same_v1_without_new_put_or_session
test_interrupted_push_resumes_without_reuploading_completed_file
test_put_success_with_lost_ack_is_reconciled_without_second_put
test_duplicate_blob_is_shared_across_paths_and_versions
test_signed_checksum_rejects_wrong_bytes_without_creating_object
test_poisoned_key_is_never_overwritten_and_operator_cleanup_recovers_same_version
test_concurrent_conditional_puts_converge_on_one_blob_and_version
test_status_separates_logical_and_unique_content_metrics
test_download_capabilities_are_sequential_replayable_and_fresh
test_expiring_get_started_in_time_completes_but_new_request_fails
test_corruption_after_ready_is_detected_without_final_pull_output
test_ready_manifest_and_entries_reject_mutation
test_pull_reconstructs_byte_identical_tree
test_concurrent_pull_publication_has_one_winner
test_api_and_cli_errors_are_actionable_and_redacted
```

For interruption, wrap the real `HttpByteTransfer` with a normal decorator that raises
`RetryableTransferError` before its second PUT. First run must leave one Blob AVAILABLE. Record that
object's `LastModified`/ETag and session count; rerun with the real transfer and require both remain
unchanged while only missing files upload.

For lost acknowledgement, call prepare/URL and complete the real PUT, deliberately skip the API
completion call, then rerun normal push. `prepare_upload` must reconcile the existing deterministic
object using size plus provider-attested checksum, mark it AVAILABLE, and leave its LastModified
unchanged. A separate adapter/service test removes the provider checksum and proves the streamed-GET
fallback. For oversized
preflight, construct the scanner/workflow with an 8-byte test limit and a 9-byte file, then assert
zero Dataset/version/session rows and zero newly tracked object keys.

For provider rejection, PUT wrong synthetic bytes with the signed expected checksum and assert 400
plus no object. For poisoned-key recovery, use the root fixture to create wrong bytes at a missing
deterministic key before normal upload. Push must return `STORED_OBJECT_MISMATCH` with
`CONTACT_OPERATOR`, leave the wrong object byte-identical, and never issue a replacing PUT. Simulate
the operator explicitly deleting that non-AVAILABLE object, rerun the original source, and require
the same Version UUID/reference to become READY. The application itself never deletes or overwrites
the poisoned object.

For post-READY corruption, root-overwrite one deterministic object key, run pull, require exit 5 and
no requested output path. READY remains terminal in PostgreSQL.

Metrics assertions use a Version with two logical paths sharing one Blob: snapshot totals count two
files while content totals count one Blob, and one AVAILABLE Blob makes both logical entries ready.
Each successful push invocation must satisfy created plus reused equals unique; a resumed invocation
reports previously AVAILABLE content as reused. READY empty progress is complete, while pre-READY
zero-denominator percentages are null. Assert no mutable progress counters exist to drift.

Capability tests prove the API never issues more than one URL, the next URL is not requested before
the current file is verified, replaying a cursor selects the same ordinal with a fresh TTL, cursors
cannot cross Versions, and no URL/query leaks to logs. Use a short TTL against the pinned MinIO image
to prove request-start expiry behavior. Concurrent final publication must leave one complete winner
and one actionable no-replace failure, never a mixed or replaced tree.

- [ ] **Step 3: Run the integrated contracts**

Run:

```bash
docker compose up -d --wait
uv run alembic upgrade head
uv run pytest tests/integration/test_transfer_flow.py \
  tests/integration/test_cli_workflow.py -q
```

Expected: every named test passes against live services. If a test exposes a composition,
serialization, or transaction mismatch, update the owning Task 4–10 module without changing
approved semantics, rerun the complete two-file command, and require every named test to pass.

- [ ] **Step 4: Run the complete suite and inspect storage/DB state**

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy robolake apps
docker compose ps
curl --fail --silent http://localhost:18000/health
git diff --check
```

Expected: no skips/failures; services healthy; static checks clean. Query PostgreSQL/MinIO from tests
to confirm logical duplicate entries share one Blob/object and no presigned URL was persisted.
Review integration-related diffs; do not commit.

### Task 12: Demo, ADRs, roadmap, and operator documentation

**Files:**
- Create: `scripts/demo-v01.sh`
- Modify: `README.md`
- Modify: `Makefile`
- Modify: `docs/PRODUCT_BRIEF.md`
- Modify: `docs/USER_STORIES.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/ARCHITECTURE_V0_1.md`
- Modify: `docs/THREAT_MODEL_V0_1.md`
- Modify: `docs/adr/0002-immutable-dataset-versions.md`
- Modify: `docs/adr/0003-object-storage-and-content-addressed-blobs.md`
- Modify: `docs/adr/0004-resumable-upload-strategy.md`
- Modify: `docs/adr/0006-m1-conditional-single-put-slice.md`
- Modify: `docs/adr/0007-failed-publication-and-manual-repair.md`
- Modify: `examples/README.md`

**Interfaces:**
- Produces: repeatable `scripts/demo-v01.sh`
- Produces: verified ADR 0006/0007 and re-baselined M1/M2 docs

- [ ] **Step 1: Write the demo script and syntax-check it**

Use this concrete flow:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
SOURCE="$WORK/source"
OUTPUT="$WORK/restored"
DATASET="demo/v01-pick-place"

cd "$ROOT"
docker compose up -d --build --wait
uv sync
uv run alembic upgrade head
uv run robolake example generate "$SOURCE" --seed 7
mkdir -p "$SOURCE/duplicate"
cp "$SOURCE/camera/front/frame-000001.bin" "$SOURCE/duplicate/same-frame.bin"
REFERENCE=$(uv run robolake push "$SOURCE" --dataset "$DATASET" | awk '/^READY / {print $2}')
test -n "$REFERENCE"
uv run robolake status "$REFERENCE"
uv run robolake manifest "$REFERENCE"
printf '\n'
uv run robolake pull "$REFERENCE" --output "$OUTPUT"
diff -r --no-dereference "$SOURCE" "$OUTPUT"
(cd "$SOURCE" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$WORK/source.sha256"
(cd "$OUTPUT" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$WORK/output.sha256"
diff -u "$WORK/source.sha256" "$WORK/output.sha256"
printf 'RoboLake v0.1 demo verified: %s\n' "$REFERENCE"
```

Run `bash -n scripts/demo-v01.sh`; expected exit 0. Make it executable. The script preserves Compose
volumes and uses only deterministic synthetic bytes.

- [ ] **Step 2: Update ADRs without duplicating content-addressing decisions**

Amend ADR 0003 to say `AVAILABLE` physical Blob bytes/identity are immutable, logical entries hold
only path/size/hash identity, and no new write URL is issued. Keep media-type hints outside the
canonical manifest and M1 persistence. ADR 0006 records create-only conditional PUT, exact signed
headers, 200/412/409 reconciliation, provider-checksum verification with streamed fallback,
single-PUT/file-level resume, the exact pre-registration limit, empty-version behavior, and
multipart deferral. ADR 0007 records immutable manifest identity, recoverable FAILED publication
state, stable error/derived-action semantics, and the manual repair boundary. Add ADR 0007 to ADR
0002's related decisions. Clarify in ADR 0004 that its multipart decision remains the M2/final-v0.1
target, while ADR 0006 authorizes the bounded M1 single-PUT slice and rejects unsupported files
before registration. Do not create ADR 0008; content addressing remains owned by ADR 0003.

- [ ] **Step 3: Rebaseline product/architecture/roadmap/threat docs**

Mark M1 complete only after the demo passes. Describe M2 solely as multipart upload with within-file
resume/reconciliation, retaining the presigned direct-byte architecture. Move cleanup/observability
and release evidence later without inventing new product capabilities. Update endpoint/schema/state
sections to actual code and record the 5,000,000,000-byte M1 limitation, Linux/macOS-only support,
one-capability pull, atomic-no-replace publication, manual poisoned-key repair, and no continuous
scrub or power-loss durability guarantee.

- [ ] **Step 4: Update README and Make targets**

Add the four exact CLI examples, empty/idempotent/resume semantics, API URL configuration, error
codes/actions, 5,000,000,000-byte per-file limit, supported platforms, and demo invocation. Add a `demo` Make target invoking
`scripts/demo-v01.sh`; keep all M0 targets. Document that `manifest` writes canonical JSON without a
newline, canonical entries contain only path/size/hash, and pull refuses an existing output.

- [ ] **Step 5: Run demo and documentation checks**

Run:

```bash
bash -n scripts/demo-v01.sh
scripts/demo-v01.sh
git diff --check
```

Expected: demo builds services, reaches READY, prints canonical manifest, reconstructs identical
bytes, and prints its verification line. A repeat run resolves the same version and skips verified
Blobs. Review scripts/docs only; do not commit.

### Task 13: Final M1 verification and scope audit

**Files:**
- Verify: all M1 changes
- Modify only if a required check exposes a real defect

**Interfaces:**
- Consumes: complete repository
- Produces: fresh evidence for every required command and acceptance criterion

- [ ] **Step 1: Run every required environment and CLI command**

Run exactly:

```bash
docker compose up -d --build --wait
uv sync
uv run alembic upgrade head
uv run robolake --help
uv run robolake push --help
uv run robolake status --help
uv run robolake manifest --help
uv run robolake pull --help
```

Expected: all exit 0; Compose services healthy; all four M1 commands documented.

- [ ] **Step 2: Run all formatting, typing, tests, and demo**

Run exactly:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy robolake apps
uv run pytest
scripts/demo-v01.sh
```

Expected: every command exits 0; pytest reports no skipped integration requirements; demo reports a
byte-identical restored tree. If a command fails, diagnose its reported defect, apply the smallest
in-scope correction, and rerun the complete command that failed rather than a reduced subset.

- [ ] **Step 3: Re-run targeted threat and lifecycle evidence**

Run:

```bash
uv run pytest \
  tests/unit/domain/test_manifest.py \
  tests/unit/infrastructure/test_scanner.py \
  tests/unit/infrastructure/test_atomic_tree.py \
  tests/unit/infrastructure/test_atomic_publish.py \
  tests/integration/test_database_constraints.py \
  tests/integration/test_presigned_transfer.py \
  tests/integration/test_transfer_flow.py -q
curl --fail --silent http://localhost:18000/health
git diff --check
```

Expected: all path, immutability, create-only concurrency, provider checksum, interruption,
corruption, dedupe, empty, cursor/TTL, and no-replace pull tests pass; health reports
application/database/object storage `ok`; no whitespace errors.

- [ ] **Step 4: Audit scope and sensitive material**

Run:

```bash
rg -n -i 'kafka|airflow|iceberg|parquet|ros2|mcap.*pars|authentication|frontend|kubernetes' \
  apps robolake pyproject.toml docker-compose.yml
git status --short
git diff --stat
git diff
```

Expected: no excluded runtime capability; documentation/examples are synthetic; no `.env`, local
dataset, credential, log, presigned URL, or absolute workstation path is tracked. Inspect all diffs
and preserve the no-commit policy.

- [ ] **Step 5: Prepare the implementation report**

Report repository structure, files changed, domain/transaction decisions, every command and result,
test counts, demo reference/result, deviations, known M1 limits, and exact M2 scope (multipart and
within-file resume only). Include the Linux/macOS filesystem-contract matrix result and distinguish
atomic visibility from power-loss durability. Do not claim completion without fresh Step 1–4 evidence and do not commit,
push, merge, or deploy without a new explicit request.
