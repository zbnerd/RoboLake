# M2-B MinIO Multipart Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the provider-neutral multipart boundary, pinned-MinIO adapter, and provider
contract/fault-injection evidence without connecting M2 API, CLI, database orchestration, or the
completion runner.

**Architecture:** Framework-free application contracts describe multipart initiation, one-part
capabilities, provider observations, conditional completion, abort, and final-object inspection.
A dedicated infrastructure adapter owns every boto3/botocore type and translates provider results
into safe immutable values and stable RoboLake failures. Live tests prove the pinned-MinIO contract;
synthetic transport tests cover AWS-documented and malformed outcomes that cannot be induced
reliably in MinIO.

**Tech Stack:** Python 3.12, boto3/botocore, httpx, pinned MinIO, pytest, Ruff, mypy.

## Global Constraints

- Baseline is develop `6574b21135de16fba91eea01f5669380d8769df8` with Alembic head
  `20260714_0003` and Accepted ADRs 0008-0010.
- Direct MPU targets the deterministic final SHA-256 key; no temporary object, overwrite, final
  delete, or automatic repair path may be added.
- Complete always carries `If-None-Match: *` and every ordered receipt carries PartNumber, ETag,
  and ChecksumSHA256.
- Provider SDK types and raw responses must not cross the infrastructure boundary. Opaque upload IDs
  and capabilities may cross only through application-private redacted contracts; their values,
  query strings, raw errors, and credentials must not appear in domain/public API models,
  repr/log/public error text, or telemetry.
- ListParts is observation only and never constructs a CompletedPartReceipt.
- M1 object-storage behavior and all M2-A domain/database semantics remain unchanged.
- No API route, CLI workflow, admission orchestration, completion runner, whole-object verifier,
  Blob transition, DatasetVersion finalization, migration, or 5 GB fixture is in scope.
- Every production behavior begins with a focused failing test and completes with a green focused
  test before the next behavior.

---

### Task 1: Provider-neutral multipart values and port

**Files:**
- Modify: `robolake/application/contracts.py`
- Modify: `robolake/application/ports.py`
- Modify: `robolake/domain/errors.py`
- Create: `tests/unit/application/test_multipart_storage_contracts.py`
- Modify: `tests/unit/test_architecture.py`

**Interfaces:**
- Produces: `ProviderUploadId`, `UploadPartCapability`, `ListedProviderPart`,
  `MultipartCompletionOutcome`, `MultipartAbortOutcome`, `FinalObjectInspection`, and
  `MultipartObjectStorePort`.
- Consumes: `PresignedRequest`, `CompletedPartReceipt`, and canonical SHA-256/Base64 utilities.

- [x] **Step 1: Write failing value/redaction tests**

  Assert provider upload IDs and capability URLs are absent from `repr`, validation rejects invalid
  part ranges and malformed provider observations, and outcome enums expose only approved values.

- [x] **Step 2: Verify RED**

  Run:

  ```bash
  uv run pytest tests/unit/application/test_multipart_storage_contracts.py -q
  ```

  Expected: collection fails because the provider-neutral contracts do not exist.

- [x] **Step 3: Add minimal immutable contracts and stable errors**

  The port exposes exactly one operation per provider primitive:

  ```python
  class MultipartObjectStorePort(Protocol):
      def create_multipart(self, object_key: str) -> ProviderUploadId: ...
      def presign_upload_part(
          self,
          object_key: str,
          upload_id: ProviderUploadId,
          part_number: int,
          size_bytes: int,
          checksum_sha256_base64: str,
          expires_seconds: int,
      ) -> UploadPartCapability: ...
      def list_parts(
          self, object_key: str, upload_id: ProviderUploadId
      ) -> tuple[ListedProviderPart, ...]: ...
      def complete_multipart(
          self,
          object_key: str,
          upload_id: ProviderUploadId,
          parts: Sequence[CompletedPartReceipt],
      ) -> MultipartCompletionResult: ...
      def abort_multipart(
          self, object_key: str, upload_id: ProviderUploadId
      ) -> MultipartAbortResult: ...
      def inspect_final_object(self, object_key: str) -> FinalObjectInspection: ...
  ```

  Provider-neutral errors distinguish contract violation, transient failure, ambiguous create/
  complete/abort, 409, 412, and NoSuchUpload without embedding provider details.

- [x] **Step 4: Verify GREEN and architecture boundary**

  Run:

  ```bash
  uv run pytest tests/unit/application/test_multipart_storage_contracts.py tests/unit/test_architecture.py -q
  ```

---

### Task 2: Receipt parsing, presigning, listing, and HEAD inspection

**Files:**
- Create: `robolake/infrastructure/multipart_storage.py`
- Create: `tests/unit/infrastructure/test_multipart_storage.py`

**Interfaces:**
- Consumes: the Task 1 port values and existing internal/public S3 clients.
- Produces: `S3MultipartObjectStore`, `parse_upload_part_response`, exact one-part presigning,
  paginated `list_parts`, and provider-owned final-object inspection.

- [x] **Step 1: Write failing receipt and presign tests**

  Cover missing/malformed/mismatching response ETag/checksum, identical receipts on different part
  numbers, exact signed Content-Length/checksum/part/upload parameters, and secret-safe repr.

- [x] **Step 2: Verify RED**

  Run:

  ```bash
  uv run pytest tests/unit/infrastructure/test_multipart_storage.py -k 'receipt or presign' -q
  ```

  Expected: import/attribute failures for the missing adapter.

- [x] **Step 3: Implement receipt parsing and one-part capability**

  Construct `CompletedPartReceipt.from_upload_response(...)` only from a parsed successful response.
  Generate one `upload_part` URL whose signed parameters include Bucket, Key, UploadId, PartNumber,
  ContentLength, and ChecksumSHA256; return exact required headers and expiration.

- [x] **Step 4: Write failing ListParts and inspection tests**

  Cover multiple pages, non-advancing markers, malformed pages, duplicate/out-of-range part numbers,
  NoSuchUpload translation, composite checksum preservation as provider metadata, and missing HEAD.

- [x] **Step 5: Implement pagination and inspection minimally**

  Every page is validated, observations are returned in part-number order, and no listing path
  imports or constructs `CompletedPartReceipt`. HEAD labels raw provider checksum/type separately
  from canonical Blob identity.

- [x] **Step 6: Verify GREEN**

  Run:

  ```bash
  uv run pytest tests/unit/infrastructure/test_multipart_storage.py -q
  ```

---

### Task 3: Conditional Complete, abort, and synthetic provider faults

**Files:**
- Modify: `robolake/infrastructure/multipart_storage.py`
- Modify: `tests/unit/infrastructure/test_multipart_storage.py`

**Interfaces:**
- Produces: conditional Complete request construction, result/error translation, and safe abort.
- Consumes: ordered `CompletedPartReceipt` values only.

- [x] **Step 1: Write failing completion request tests**

  Assert sorting, gap/duplicate rejection, ETag+ChecksumSHA256 on every part, outgoing
  `If-None-Match: *`, no unconditional call, and no upload ID in errors.

- [x] **Step 2: Verify RED**

  Run:

  ```bash
  uv run pytest tests/unit/infrastructure/test_multipart_storage.py -k 'complete or conditional' -q
  ```

- [x] **Step 3: Implement minimal conditional completion**

  The adapter calls the parsed SDK operation once, returns `COMPLETED` only for a structurally valid
  success result, maps 412 and 409 into explicit outcomes, maps NoSuchUpload separately, and never
  retries 409 or mutates application/database state.

- [x] **Step 4: Add failing synthetic fault tests**

  Inject create response loss, malformed create, UploadPart response loss, embedded error parsed as
  failure, 409, complete response loss, timeouts/resets, malformed listing/HEAD, token loops, and
  abort ambiguity. Each test is labeled `AWS_DOCUMENTED_SYNTHETIC` or
  `PROVIDER_CONTRACT_DEFENSIVE`.

- [x] **Step 5: Implement narrow sanitized translation**

  Translation consumes only provider exception categories/status/codes needed for stable outcomes.
  Exception messages contain operation category and a deterministic key fingerprint, never body,
  URL, bucket, upload ID, or credentials.

- [x] **Step 6: Verify GREEN**

  Run:

  ```bash
  uv run pytest tests/unit/infrastructure/test_multipart_storage.py -q
  ```

---

### Task 4: Pinned-MinIO live contract and cleanup evidence

**Files:**
- Create: `tests/integration/test_m2_provider_contract.py`
- Modify: `tests/integration/conftest.py` only if a shared randomized-prefix cleanup fixture is needed
- Modify: `docs/M2_PROVIDER_PROBES.md` only for newly observed implementation evidence

**Interfaces:**
- Consumes: `S3MultipartObjectStore`, the exact Compose-pinned MinIO image, and httpx for part PUT.
- Produces: live evidence for Create, signed UploadPart, receipt, List, conditional Complete, 412
  concurrency, Abort, HEAD, and zero test residue.

- [x] **Step 1: Write isolated live tests**

  Each test uses a randomized `blobs/sha256/` prefix and a cleanup fixture that aborts only matching
  incomplete MPUs and deletes only matching completed objects.

- [x] **Step 2: Verify RED against pinned MinIO**

  Run:

  ```bash
  docker compose up -d --wait
  uv run pytest tests/integration/test_m2_provider_contract.py -q
  ```

  Expected: adapter gaps surface as focused failures; no test state survives cleanup.

- [x] **Step 3: Close provider-specific gaps without changing ADR semantics**

  Prove exact/changed/omitted checksum and length behavior, same-number replacement, identical-part
  receipts, lost-receipt exact re-upload, actual outgoing conditional header, normal Complete,
  two-MPU 200/412 convergence, repeated completion translation, abort/NoSuchUpload, HEAD metadata,
  and final absence of incomplete MPUs/objects.

- [x] **Step 4: Record only observed evidence**

  Keep live MinIO, AWS-documented synthetic, and defensive classifications separate. Do not claim
  live AWS, packet-level behavior, whole-object canonical integrity, or M2 orchestration.

- [x] **Step 5: Verify the focused provider suite**

  Run:

  ```bash
  uv run pytest tests/unit/infrastructure/test_multipart_storage.py \
    tests/integration/test_m2_provider_contract.py -q
  ```

---

### Task 5: Full regression, scope review, and delivery

**Files:**
- Review every changed path; no production API/CLI/runner or migration file may appear. The existing
  API error-envelope test may verify that a provider-neutral failure remains secret-safe.

- [x] **Step 1: Run all required verification**

  ```bash
  uv sync --locked
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy robolake apps scripts/benchmark_m1.py
  uv run pytest
  uv run pytest -q --no-cov tests/integration/test_migrations.py tests/integration/test_m2_migration.py
  scripts/demo-v01.sh
  ```

- [x] **Step 2: Prove cleanup and redaction**

  Query the pinned provider for the randomized test prefixes, scan captured logs/diffs for URLs,
  credentials, upload IDs, local paths, and verify v0.1.0/Alembic targets remain unchanged.

- [ ] **Step 3: Review diff and commit coherent Conventional Commits**

  Prefer:

  ```text
  feat(m2): add multipart storage port
  feat(m2): add MinIO multipart adapter
  test(m2): verify multipart provider contracts
  ```

  Use fewer commits if separating them would create a broken intermediate tree.

- [ ] **Step 4: Push and create one PR targeting develop**

  Branch: `feature/m2-minio-adapter`

  Title: `feat(m2): add MinIO multipart provider adapter`

  The PR separates pinned-MinIO live evidence, AWS-documented synthetic evidence, defensive checks,
  and explicit non-goals. It is not merged and does not start M2-C or M2-D.

## Self-review checklist

- Every M2-B requirement maps to a focused test or an explicitly unverified AWS assumption.
- Application/domain files import no provider SDK module or provider response type.
- `ProviderUploadId` and presigned URL query strings are absent from repr/log/error paths.
- ListParts observations and successful UploadPart receipts remain distinct types.
- Complete always sends an ordered ETag+ChecksumSHA256 receipt list and conditional create-only
  header; 409 is never retried automatically.
- Live cleanup affects only randomized test-owned prefixes and ends with zero incomplete MPU/object
  residue.
- No database, API, CLI, completion-runner, final verification, publication, or M1 behavior change
  is present.
