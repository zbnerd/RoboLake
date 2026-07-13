# M2 Resumable Multipart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add resumable multipart push for one Blob above 5,000,000,000 bytes while preserving every
released M1 identity, publication, integrity, pull, and error invariant.

**Architecture:** The API allocates generation-numbered provider attempts directly at the immutable
final SHA-256 key, persists exact canonical part-plan bytes, reconciles stored UploadPart response
receipts with ListParts, and issues a bounded rolling capability window. Completion is accepted as
durable PostgreSQL work and executed by a same-artifact server runner under a separate completion
lease. Conditional completion publishes at most one object; whole-object SHA-256 is mandatory before
AVAILABLE. Expiring upload admission fences CLI mutations while the persistent session/MPU remains
resumable.

**Tech Stack:** Python 3.12, FastAPI, Typer, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 17,
boto3/botocore, pinned MinIO, httpx, pytest, Hypothesis (add only if used for the state properties),
Ruff, and mypy.

## Global Constraints

- After PR #5 is merged, treat ADRs 0008–0010 as the accepted implementation contract; revise them
  only through a new ADR.
- Canonical manifest v1, Blob SHA-256/final key, DatasetVersion identity, READY/AVAILABLE meaning,
  M1 single PUT, pull, Linux/macOS, metrics, and error envelope remain frozen.
- Multipart activates only when `size_bytes > 5_000_000_000`.
- Base part is 67,108,864 bytes; maximum part is 5,368,709,120 bytes; maximum 10,000 parts; maximum
  Blob is 5,000,000,000,000 bytes.
- Final Complete always carries `If-None-Match: *`; every new multipart final object is streamed and
  whole-file SHA-256 verified before AVAILABLE.
- One immutable request UUID maps to one response/session; invocation UUID and Blob-scoped session
  generation have distinct meanings. Terminal generations never reactivate.
- UploadPart response ETags and normalized SHA-256 checksums are retained. ListParts verifies current
  provider state but never supplies a missing completion receipt; receipt loss forces exact-part
  re-upload. A supported provider must return the requested UploadPart checksum.
- Completion returns 202, releases upload admission, and runs from PostgreSQL under a distinct
  heartbeat/fencing lease. Do not add Kafka, Celery, or another queue.
- Provider upload ID is never an independent stable API field; it may appear only inside a presigned
  capability URL whose entire query, along with absolute paths, provider bodies, and credentials,
  is redacted from logs/errors/telemetry and never interpreted by the client.
- No multipart bytes pass through FastAPI; no temp-key architecture, final deletion, repair, Blob
  GC, pull resume, authentication, or excluded platform/data technology.
- The first M2 deployment is offline: drain and stop every v0.1.0 server before migration; mixed
  v0.1.0/M2 serving is unsupported.
- Implement red-green-refactor. Run focused tests after each step and the full suite before every
  review gate.

---

## Planned file map

| File | Responsibility |
| --- | --- |
| `robolake/domain/multipart.py` | Pure part-size algorithm, canonical plan encoder/hash, identities, state transitions. |
| `robolake/domain/constants.py` | M2 protocol constants only. |
| `robolake/domain/errors.py` | Stable M2 error classes/codes. |
| `robolake/domain/records.py` | Framework-free session/part records. |
| `robolake/application/contracts.py` | Part plans, status, capability window, invocation metrics. |
| `robolake/application/ports.py` | Registry/provider/local transfer interfaces. |
| `robolake/application/multipart.py` | Prepare, initiate, reconcile, issue, receipt confirm, accept-complete, abort use cases. |
| `robolake/application/multipart_completion.py` | Claim, conditional Complete, reconcile, full verification, fenced completion state. |
| `robolake/application/admission.py` | Acquire/renew fenced multipart admission leases. |
| `robolake/application/push.py` | Select M1/M2 and orchestrate rolling-window resume. |
| `robolake/infrastructure/models.py` | SQLAlchemy request/session/part/upload-lease/completion-lease mappings. |
| `robolake/infrastructure/store.py` | Transactional generation resolver, structural persistence, atomic lease fences/claims. |
| `robolake/infrastructure/object_storage.py` | MPU control, paginated ListParts, exact presigning, conditional Complete, abort. |
| `robolake/infrastructure/scanner.py` | One stable pass producing full and part hashes for large files. |
| `robolake/infrastructure/http_transfer.py` | Positional bounded UploadPart streaming/outcome only. |
| `robolake/infrastructure/api_client.py` | Strict M2 API response parsing without provider details. |
| `robolake/infrastructure/settings.py` | Operational concurrency/lease limits with startup validation. |
| `apps/api/schemas/transfers.py` | Strict multipart requests/responses. |
| `apps/api/routes/transfers.py` | Thin session endpoints. |
| `apps/api/errors.py` | Safe stable action mapping. |
| `apps/completion_runner/main.py` | Same-artifact PostgreSQL completion loop; not a separate service boundary. |
| `docker-compose.yml` | Run the same versioned artifact as API plus a supervised completion-runner process. |
| `apps/cli/commands/datasets.py`, `apps/cli/main.py`, `apps/cli/output.py` | Existing push UX, progress, optional concurrency, narrow abort. |
| `migrations/versions/<revision>_m2_multipart.py` | Additive schema, triggers, guarded downgrade. |
| `config/minio/robolake-policy.template.json` | Existing MPU actions verified; no DeleteObject/final repair permission. |
| `tests/unit/**`, `tests/integration/**` | Protocol, state, DB, provider, API/CLI, fault, regression tests. |
| `scripts/demo-m2.sh`, `scripts/benchmark-m2.py` | Manual >5 GB interruption/resume evidence only. |
| `docs/M2_POISONED_FINAL_KEY_RUNBOOK.md` | Evidence-preserving manual recovery boundary; no automatic repair. |

### Task 1: Freeze the multipart protocol in the domain

**Files:**
- Create: `robolake/domain/multipart.py`
- Modify: `robolake/domain/constants.py`
- Modify: `robolake/domain/errors.py`
- Modify: `robolake/domain/records.py`
- Test: `tests/unit/domain/test_multipart.py`
- Test: `tests/unit/domain/test_lifecycle.py`

**Interfaces:**
- Produces: `plan_multipart(size_bytes: int) -> MultipartPlan`, `MultipartPart`,
  `canonical_part_plan_bytes(plan: HashedMultipartPlan) -> bytes`, `MultipartSessionState`,
  `UploadPartState`, typed request/invocation/session-generation IDs, and M2 domain errors.
- Consumes: existing `Sha256Digest`, Blob/UploadSession records, and illegal-transition style.

- [ ] **Step 1: Write formula and boundary tests**

```python
@pytest.mark.parametrize(
    ("size", "part_size", "count", "final_size"),
    [
        (5_000_000_001, 67_108_864, 75, 33_944_065),
        (10 * 1024**3, 67_108_864, 160, 67_108_864),
        (100 * 1024**3, 67_108_864, 1_600, 67_108_864),
        (1024**4, 134_217_728, 8_192, 134_217_728),
        (5_000_000_000_000, 536_870_912, 9_314, 121_196_544),
    ],
)
def test_plan_multipart_boundaries(size: int, part_size: int, count: int, final_size: int) -> None:
    plan = plan_multipart(size)
    assert plan.part_size_bytes == part_size
    assert len(plan.parts) == count
    assert plan.parts[-1].size_bytes == final_size
    assert sum(part.size_bytes for part in plan.parts) == size
```

- [ ] **Step 2: Run red tests**

Run: `uv run pytest tests/unit/domain/test_multipart.py -q`

Expected: collection fails because `robolake.domain.multipart` does not exist.

Add golden-vector tests for the exact schema-v1 canonical JSON bytes/hash: fixed field order, compact
UTF-8, no trailing newline, JSON integers only, ascending part number, lowercase digests. Mutate every
field and reject alternate whitespace/order, duplicate/unknown fields, floats, booleans, and uppercase
hex. The server hashes validated typed fields rather than caller serialization.

- [ ] **Step 3: Implement exact pure values and formula**

```python
@dataclass(frozen=True, slots=True)
class MultipartPart:
    number: int
    offset_bytes: int
    size_bytes: int

@dataclass(frozen=True, slots=True)
class MultipartPlan:
    size_bytes: int
    part_size_bytes: int
    parts: Sequence[MultipartPart]

def plan_multipart(size_bytes: int) -> MultipartPlan:
    if not 5_000_000_000 < size_bytes <= 5_000_000_000_000:
        raise UnsupportedFileSizeError("multipart", size_bytes)
    part_size = 67_108_864
    while math.ceil(size_bytes / part_size) > 10_000 and part_size < 5_368_709_120:
        part_size = min(part_size * 2, 5_368_709_120)
    count = math.ceil(size_bytes / part_size)
    if count > 10_000:
        raise UnsupportedFileSizeError("multipart", size_bytes)
    return MultipartPlan(
        size_bytes,
        part_size,
        tuple(
            MultipartPart(i, (i - 1) * part_size, min(part_size, size_bytes - (i - 1) * part_size))
            for i in range(1, count + 1)
        ),
    )
```

Place numeric values behind named constants. Add enum transition tables exactly matching
`docs/M2_STATE_MACHINES.md`; do not modify M1 enum values.

- [ ] **Step 4: Add error and transition tests**

Assert `LOCAL_FILE_CHANGED`, `INVALID_PART_NUMBER`, `PART_SIZE_MISMATCH`,
`PART_CHECKSUM_REJECTED`, `MULTIPART_SESSION_NOT_FOUND`, `ADMISSION_LEASE_HELD`,
`ADMISSION_CAPACITY_EXHAUSTED`, `ADMISSION_LEASE_LOST`,
`MULTIPART_INITIATION_IN_PROGRESS`, `MULTIPART_INITIATION_AMBIGUOUS`,
`MULTIPART_COMPLETION_AMBIGUOUS`, and `FINAL_BLOB_PUBLICATION_CONFLICT`. Generate every enum pair and
assert only documented edges succeed. Include `CREATED -> INITIATING -> IN_PROGRESS`,
`CREATED -> CANCELLED`, terminal initiation ambiguity, and guarded `COMPLETING -> IN_PROGRESS`;
prove 409/`NoSuchUpload` with no final terminate the generation. `UploadPartState` contains only
`PENDING`, `UPLOADED`, and `VERIFIED`; capability issuance is not a state.

- [ ] **Step 5: Run and commit**

Run: `uv run pytest tests/unit/domain/test_multipart.py tests/unit/domain/test_lifecycle.py -q`

Expected: all selected tests pass.

```bash
git add robolake/domain tests/unit/domain
git commit -m "feat(m2): define multipart protocol"
```

### Task 2: Add database-enforced session and part invariants

**Files:**
- Create: `migrations/versions/202607xx_0003_m2_multipart.py`
- Modify: `robolake/infrastructure/models.py`
- Modify: `robolake/infrastructure/store.py`
- Test: `tests/integration/test_m2_migration.py`
- Test: `tests/integration/test_m2_database_invariants.py`
- Test: `tests/integration/test_m2_admission_leases.py`
- Test: `tests/integration/test_m2_completion_leases.py`
- Test: `tests/unit/infrastructure/test_store.py`

**Interfaces:**
- Consumes: domain session/part states and `MultipartPlan` from Task 1.
- Produces: generation/request mappings, `UploadPartModel`, `MultipartAdmissionLeaseModel`,
  `MultipartCompletionLeaseModel`, fenced repository operations, work claims, plan reconciliation,
  and guarded offline migration/downgrade.

- [ ] **Step 1: Write migration-from-M1 and direct-SQL failure tests**

Start from revision `20260711_0002`, insert a valid M1 SINGLE_PUT row, upgrade, and assert it remains
unchanged. Add SQL assertions for positive/unique Blob-scoped generations, immutable request
bindings, exact canonical plan hash, initiation/provider-ID gates, boundary mutation after committed
`CREATED` registration, part
reparenting, VERIFIED without response+listing receipt agreement, COMPLETING with a missing part,
unguarded recovery, invalid `PARTS_READY`/`FINAL_PRESENT` gates, and COMPLETED without structurally
consistent full-stream evidence. Prove
different part numbers may share expected digest/checksum/ETag while duplicate number and
overlapping range remain invalid.

Add upload lease tests for idempotent acquire request, invocation binding, 64 abandoned/expired rows,
atomic same-session takeover, monotonic epoch, current-owner renewal, stale-owner atomic SQL
rejection, and database-time expiry. Add completion-lease tests for one claim, separate capacity,
independent heartbeat, stale runner rejection, and takeover after expiry. Direct structural SQL tests
must not claim that PostgreSQL independently knows the caller identity.

- [ ] **Step 2: Verify red state**

Run: `uv run pytest tests/integration/test_m2_migration.py tests/integration/test_m2_database_invariants.py -q`

Expected: tests fail because the M2 revision/table/columns do not exist.

- [ ] **Step 3: Implement the additive migration**

Create the exact columns/checks/triggers in `docs/M2_MIGRATION_AND_ROLLBACK.md`. Model the table as:

```python
class UploadPartModel(Base):
    __tablename__ = "upload_parts"
    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="RESTRICT"), primary_key=True
    )
    part_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset_bytes: Mapped[int] = mapped_column(BigInteger)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    upload_response_etag: Mapped[str | None] = mapped_column(Text)
    upload_response_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    upload_response_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listed_etag: Mapped[str | None] = mapped_column(Text)
    listed_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    listed_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    provider_listed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_capability_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_capability_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capability_issue_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

class MultipartAdmissionLeaseModel(Base):
    __tablename__ = "multipart_admission_leases"
    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    invocation_id: Mapped[UUID] = mapped_column(Uuid)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    epoch: Mapped[int] = mapped_column(BigInteger)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class MultipartCompletionLeaseModel(Base):
    __tablename__ = "multipart_completion_leases"
    upload_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    owner_instance_id: Mapped[UUID] = mapped_column(Uuid)
    epoch: Mapped[int] = mapped_column(BigInteger)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

Add session generation, plan schema/hash, initiation ambiguity, completion-work phase, immutable
verification evidence, and same-MPU reconciliation fields from the migration design.
`UploadPart.state` allows only PENDING, UPLOADED, VERIFIED; capability metadata is diagnostic only.

- [ ] **Step 4: Implement transactional repository methods**

Define these exact interfaces:

| Method | Signature |
| --- | --- |
| Create/resolve | `create_or_resolve_multipart(request_id: UUID, version_id: UUID, blob_id: UUID, plan: HashedMultipartPlan) -> MultipartUploadContext` |
| Acquire/renew upload lease | `acquire_multipart_lease(request_id: UUID, invocation_id: UUID, session_id: UUID, ttl_seconds: int, capacity: int) -> AdmissionLease` |
| Fenced upload mutation | `mutate_multipart_session(session_id: UUID, owner_id: UUID, epoch: int, command: MultipartMutation) -> MultipartUploadContext` |
| Begin initiation | `begin_provider_initiation(session_id: UUID, owner_id: UUID, epoch: int) -> None` |
| Provider acknowledgement | `record_provider_upload(session_id: UUID, owner_id: UUID, epoch: int, provider_upload_id: str) -> None` |
| Part reconciliation | `reconcile_parts(session_id: UUID, owner_id: UUID, epoch: int, provider_parts: Sequence[ProviderPart], attribution: PartAttribution) -> None` |
| Accept completion | `accept_completion(session_id: UUID, owner_id: UUID, epoch: int, request_id: UUID) -> AcceptedCompletion` |
| Claim completion | `claim_completion(owner_instance_id: UUID, ttl_seconds: int, capacity: int) -> CompletionClaim | None` |
| Heartbeat completion | `renew_completion_lease(session_id: UUID, owner_instance_id: UUID, epoch: int) -> None` |
| Fenced completion write | `record_completion_observation(claim: CompletionClaim, observation: CompletionObservation) -> None` |
| Guarded recovery | `recover_partial_completion(claim: CompletionClaim, evidence: PartialCompletionEvidence) -> None` |

Lock the Blob to allocate `max(generation)+1`; replay request bindings first and resolve uniqueness
races by binding the winner. Never persist derivable counters. Upload lease acquisition uses an
advisory lock and DB time; every application mutation is one atomic owner/epoch/expiry-fenced SQL
statement. Completion claims use `FOR UPDATE SKIP LOCKED` or equivalent and a separate lease. All
provider calls happen outside DB transactions; results are fenced again before commit.

- [ ] **Step 5: Verify upgrade, constraints, downgrade guard, and M1 regression**

Run:

```bash
uv run pytest tests/integration/test_m2_migration.py tests/integration/test_m2_database_invariants.py tests/integration/test_m2_admission_leases.py tests/integration/test_m2_completion_leases.py tests/unit/infrastructure/test_store.py -q
uv run pytest tests/integration/test_database_constraints.py -q
```

Expected: all selected tests pass; request/generation races converge; 64 expired upload leases do not
exhaust admission; one completion claim/fence wins; downgrade refuses while multipart
session/part/request/upload-lease/completion-lease rows exist and succeeds only after documented
archive/removal.

- [ ] **Step 6: Commit**

```bash
git add migrations robolake/infrastructure/models.py robolake/infrastructure/store.py tests
git commit -m "feat(m2): persist multipart sessions and parts"
```

### Task 3: Implement the provider multipart contract

**Files:**
- Modify: `robolake/application/ports.py`
- Modify: `robolake/application/contracts.py`
- Modify: `robolake/infrastructure/object_storage.py`
- Modify: `config/minio/robolake-policy.template.json`
- Test: `tests/unit/infrastructure/test_object_storage.py`
- Test: `tests/integration/test_m2_provider_contract.py`

**Interfaces:**
- Produces: `MultipartObjectStore` methods below and distinct `UploadPartReceipt`,
  `ListedProviderPart`, `CompletedPartReceipt`, and `CompleteOutcome` contracts.
- Consumes: exact bucket/key/session/part inputs; never domain-manages provider error bodies.

- [ ] **Step 1: Port the provider probes into failing integration tests**

Create isolated synthetic keys and test initiation, presigned exact headers, ListParts, replacement,
checksum rejection, two different part numbers with identical bytes/ETag/checksum, conditional
200/412 race, lost/repeated completion, Abort/NoSuchUpload, and no log leakage. Feed a fake HTTP 200
response with an embedded `<Error>` element through the adapter and assert it returns failure rather
than `CompleteOutcome.CREATED`. Preserve the negative
unguarded-overwrite proof as a probe test that never runs against non-ephemeral buckets.

Assert the adapter captures ETag/checksum from UploadPart success independently of ListParts. Add a
contract test proving Complete receives ordered stored response ETags. A matching ListParts-only ETag
must not satisfy the Complete input contract. Document this as AWS-official but not live-AWS-tested.

- [ ] **Step 2: Define the port**

| Method | Exact signature |
| --- | --- |
| Initiate | `create_multipart(object_key: str) -> str` |
| List | `list_parts(object_key: str, provider_upload_id: str) -> Sequence[ListedProviderPart]` |
| Presign | `presign_part(object_key: str, provider_upload_id: str, part_number: int, size_bytes: int, checksum_base64: str, expires_seconds: int) -> PresignedRequest` |
| Complete | `complete_multipart(object_key: str, provider_upload_id: str, parts: Sequence[CompletedPartReceipt]) -> CompleteOutcome` |
| Abort | `abort_multipart(object_key: str, provider_upload_id: str) -> None` |

`list_parts` must follow every `IsTruncated` marker. `complete_multipart` always passes
`IfNoneMatch="*"`, requires an SDK/adapter parsed success result rather than status 200 alone, and
translates embedded error/412/409/NoSuchUpload into typed outcomes. It never returns a provider body.

- [ ] **Step 3: Run red tests**

Run: `uv run pytest tests/unit/infrastructure/test_object_storage.py tests/integration/test_m2_provider_contract.py -q`

Expected: new tests fail because the port/adapter methods do not exist.

- [ ] **Step 4: Implement minimal adapter and policy**

Use `ChecksumAlgorithm="SHA256"` at initiation; sign exact part length/checksum; capture UploadPart
response ETag/checksum as opaque completion receipt; parse listed provider SHA-256 separately into
canonical hex. Keep existing final prefix and
permissions. Confirm policy grants Create/Upload/Complete through `s3:PutObject`, ListParts, List MPU,
and Abort, but does not add DeleteObject.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/infrastructure/test_object_storage.py tests/integration/test_m2_provider_contract.py -q
uv run ruff check robolake/infrastructure/object_storage.py tests/integration/test_m2_provider_contract.py
```

Expected: every pinned-MinIO contract passes and no URL appears in captured output.

```bash
git add robolake/application robolake/infrastructure/object_storage.py config/minio tests
git commit -m "feat(m2): add multipart storage adapter"
```

### Task 4: Build prepare, reconcile, and rolling capability use cases

**Files:**
- Create: `robolake/application/multipart.py`
- Create: `robolake/application/admission.py`
- Modify: `robolake/application/transfers.py`
- Modify: `robolake/application/contracts.py`
- Modify: `robolake/application/ports.py`
- Test: `tests/unit/application/test_multipart.py`
- Test: `tests/unit/application/test_admission.py`
- Test: `tests/unit/application/test_transfers.py`

**Interfaces:**
- Produces: `MultipartTransferService.prepare`, `initiate`, `status`, `reconcile`,
  `issue_capabilities`, and `confirm_part`, plus admission acquire/renew/takeover.
- Consumes: repository from Task 2 and storage port from Task 3.

- [ ] **Step 1: Write disagreement and lease tests**

Use fakes where DB/provider parts differ. A receipt-backed provider match becomes VERIFIED/reused;
a matching listed part without stored response receipt remains unresolved and is re-uploaded.
Absence/mismatch schedules exact replacement. Prove immutable request replay, distinct invocation,
one active generation under race, new generation only after terminal attempt, and no request
rebinding. Exercise `CREATED -> INITIATING -> IN_PROGRESS`, cancellation without provider call,
abort rejection while INITIATING, response-loss ambiguity, best-effort known-ID abort, and no guessed
upload ID. After initiating-owner lease loss, a new owner must terminalize ambiguity rather than
repeat Create in that generation. Add 64 expired upload leases, one reacquisition winner, stale mutation failure, and late
exact provider write reconciliation.

- [ ] **Step 2: Write rolling-window tests**

```python
def test_issue_capabilities_never_exceeds_concurrency() -> None:
    result = service.issue_capabilities(session_id, owner_id, epoch, (1, 2, 3, 4, 5), limit=4)
    assert [item.part_number for item in result.items] == [1, 2, 3, 4]
    assert all("X-Amz-" not in repr(item.request) for item in result.items)
```

Also reject duplicate/out-of-plan/already-verified part numbers and any limit outside `1..16`.

- [ ] **Step 3: Implement the service**

| Method | Exact signature |
| --- | --- |
| Prepare | `prepare(request_id: UUID, version_id: UUID, blob_sha256: Sha256Digest, part_checksums: Sequence[Sha256Digest]) -> MultipartPreparation` |
| Initiate | `initiate(session_id: UUID, owner_id: UUID, epoch: int) -> MultipartStatus` |
| Acquire/renew | `acquire_lease(request_id: UUID, invocation_id: UUID, session_id: UUID) -> AdmissionLease` |
| Reconcile | `reconcile(session_id: UUID, owner_id: UUID, epoch: int, phase: ReconciliationPhase) -> MultipartStatus` |
| Issue | `issue_capabilities(session_id: UUID, owner_id: UUID, epoch: int, part_numbers: Sequence[int], limit: int) -> PartCapabilityWindow` |
| Confirm | `confirm_part(session_id: UUID, owner_id: UUID, epoch: int, part_number: int, outcome: UploadPartOutcome) -> MultipartStatus` |

Server recomputes canonical plan bytes/hash from Blob size and exactly one checksum per part. Prepare
first returns an AVAILABLE Blob unchanged, replays immutable request mapping, then binds/creates the
active generation. Initiate commits `INITIATING` before provider I/O and stores ID before
`IN_PROGRESS`. Confirm accepts an untrusted UploadPart response receipt, stores it, then calls
ListParts; only equality with current provider facts makes VERIFIED. A ListParts-only match forces
re-upload. Initial receipt-backed matches are reused; unambiguous current responses are new;
ambiguous/later discoveries are reconciled. Capability issuance updates diagnostics only.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/unit/application/test_multipart.py tests/unit/application/test_admission.py tests/unit/application/test_transfers.py -q`

Expected: all failure-matrix assertions and idempotency replays pass.

```bash
git add robolake/application tests/unit/application
git commit -m "feat(m2): orchestrate multipart reconciliation"
```

### Task 5: Hash and stream stable local parts with bounded workers

**Files:**
- Modify: `robolake/infrastructure/scanner.py`
- Modify: `robolake/infrastructure/http_transfer.py`
- Modify: `robolake/application/push.py`
- Modify: `robolake/application/contracts.py`
- Test: `tests/unit/infrastructure/test_scanner.py`
- Test: `tests/unit/infrastructure/test_http_transfer.py`
- Test: `tests/unit/application/test_push.py`
- Test: `tests/integration/test_m2_local_source.py`

**Interfaces:**
- Produces: `HashedMultipartPlan`, safe positional part reader, bounded rolling-window Push workflow.
- Consumes: immutable `LocalFileRef`, M2 service port, and M1 progress/output contracts.

- [ ] **Step 1: Write stable-pass and mutation tests**

Generate a small test-only multipart threshold through dependency injection, not by changing protocol
constants. Assert full and part SHA values come from one pass; visible metadata change, inode swap,
truncate, and same-metadata byte mutation on a resumed invocation all stop before the first new PUT.

- [ ] **Step 2: Write bounded streaming tests**

Use a recording transport with four barriers. Assert no more than concurrency requests/buffers are
active, each request reads exactly its positional range in 1 MiB chunks, a failing worker stops new
scheduling, in-flight workers settle, and the complete window is reconciled. Capture the successful
UploadPart response ETag/checksum as an opaque `UploadPartOutcome`; simulate response loss and prove
the API cannot fabricate that receipt from the later listing.

- [ ] **Step 3: Implement pure streaming contracts**

```python
@dataclass(frozen=True, slots=True)
class HashedPart:
    number: int
    offset_bytes: int
    size_bytes: int
    sha256: Sha256Digest

@dataclass(frozen=True, slots=True)
class HashedMultipartPlan:
    full_sha256: Sha256Digest
    part_size_bytes: int
    parts: Sequence[HashedPart]
```

Hash full and part digests during the scanner's existing sequential read. Use `os.pread` or an
equivalent positional reader so workers never share a mutable seek offset. Rehash each outgoing part
and compare before accepting transfer success. After all parts resolve, perform another stable
whole-file SHA-256 pass and require the sealed digest before calling Complete.

- [ ] **Step 4: Extend Push selection without changing M1**

For each unique Blob: use existing M1 at `<= 5_000_000_000`; otherwise prepare, acquire/renew upload
admission, initiate a CREATED generation under that fence, reconcile, run rolling upload, and confirm with the current
fencing epoch. Keep Dataset registration before transfer but ensure the large-file plan is fully
validated before provider session mutation. Lease loss stops new scheduling and DB mutation;
already-started exact part PUTs settle and are reconciled by the next owner.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/infrastructure/test_scanner.py tests/unit/infrastructure/test_http_transfer.py tests/unit/application/test_push.py tests/integration/test_m2_local_source.py -q
uv run pytest tests/unit/application/test_push.py -q
```

Expected: M2 bounded/mutation tests and all M1 Push tests pass.

```bash
git add robolake/infrastructure robolake/application tests
git commit -m "feat(m2): stream bounded resumable parts"
```

### Task 6: Implement asynchronous conditional completion and whole-byte verification

**Files:**
- Modify: `robolake/application/multipart.py`
- Create: `robolake/application/multipart_completion.py`
- Modify: `robolake/infrastructure/store.py`
- Modify: `robolake/infrastructure/object_storage.py`
- Modify: `robolake/infrastructure/settings.py`
- Create: `apps/completion_runner/main.py`
- Modify: `docker-compose.yml`
- Test: `tests/unit/application/test_multipart_completion.py`
- Test: `tests/integration/test_m2_completion.py`
- Test: `tests/integration/test_m2_concurrency.py`
- Test: `tests/integration/test_m2_completion_runner.py`

**Interfaces:**
- Produces: `accept_completion(...) -> AcceptedCompletion`, a bounded PostgreSQL completion runner,
  and the only M2 path to `Blob AVAILABLE`.
- Consumes: receipt-backed parts, completion claims/leases, conditional outcomes, streamed
  `iter_bytes`.

- [ ] **Step 1: Write rows 5–13 and 17–18 as failing tests**

Cover 202 response/API-loss, client disconnect, provider-response loss, embedded error inside HTTP
200, repeated Complete, matching/mismatching final, two provider attempts, and 412.
Explicitly test all four recovery cases: matching final; final absent with same MPU/all parts;
final absent with same MPU/valid subset taking guarded COMPLETING regression; and 409 or
`NoSuchUpload` with no final requiring a new request, generation, all-PENDING plan, and ID. Inject a final object whose
composite checksum matches the part plan but whole SHA differs; assert it never becomes AVAILABLE.
Also make a `FINAL_PRESENT` object disappear before the read and prove same-MPU requeue or terminal
new-generation handling without Complete under the stale adoption reason.
Prove receipt validation requires both UploadPart response ETag and SHA-256 checksum, and Complete
receives stored response ETags, never ListParts-only ETags. Hold provider
Complete and full GET longer than API/upload lease limits while independent completion heartbeats
keep ownership. Kill a runner, expire/take over its completion lease, restart full verification at
byte zero, and prove stale evidence/AVAILABLE/terminal writes cannot commit. Two runners racing must
claim one row once.

- [ ] **Step 2: Verify red state**

Run: `uv run pytest tests/unit/application/test_multipart_completion.py tests/integration/test_m2_completion.py tests/integration/test_m2_concurrency.py tests/integration/test_m2_completion_runner.py -q`

Expected: failures show no durable acceptance/runner/completion-lease orchestration exists.

- [ ] **Step 3: Implement completion decision order**

```python
def accept_completion(
    self, session_id: UUID, owner_id: UUID, epoch: int, request_id: UUID
) -> AcceptedCompletion:
    reason = self._resolve_final_present_or_require_all_parts(session_id, owner_id, epoch)
    return self.registry.accept_completion_and_release_upload_lease(
        session_id, owner_id, epoch, request_id, reason
    )

def run_one_completion(self, owner_instance_id: UUID) -> bool:
    claim = self.registry.claim_completion(owner_instance_id)
    if claim is None:
        return False
    self._reconcile_final_or_complete(claim)
    return True
```

Acceptance atomically records `COMPLETING`/`PENDING_CLAIM`, releases upload admission, and yields HTTP
202. The runner uses the same versioned artifact and PostgreSQL rows as its durable work registry;
do not introduce an external queue. Claim with `FOR UPDATE SKIP LOCKED` or equivalent, enforce
separate completion concurrency, and heartbeat in short independent transactions at most TTL/3.

`_reconcile_final_or_complete` checks AVAILABLE/final key first. It calls Complete only for
`PARTS_READY`, supplying ordered stored UploadPart response ETags and `If-None-Match: *`;
`FINAL_PRESENT` never pretends incomplete parts are resolved. If that final observation disappears,
reconcile and requeue safely; a later Complete requires new `PARTS_READY` acceptance. It streams `iter_bytes` into SHA-256
and only then records immutable `FULL_STREAM_SHA256` evidence and commits AVAILABLE+COMPLETED under
the current completion fence. PostgreSQL checks evidence structure; integration tests prove the real
read. On partial same-MPU non-409, take only the guarded recovery edge and release completion
ownership so a CLI can reacquire upload admission. On 409/`NoSuchUpload` with absent final, terminate
the generation; a later new request creates generation+1 with no adopted receipts. On 412, verify the
existing final. On mismatch, stop without delete/overwrite.

- [ ] **Step 4: Add explicit proof that final verification reads every byte once**

The fake iterator records offsets/chunks; live test corrupts one byte and asserts exit path 5. Assert
an AVAILABLE Blob performs zero provider reads and a new multipart Blob performs one full read.
Interrupted verification plus takeover performs a second read from byte zero; it never resumes by
Range or persists live byte progress.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/unit/application/test_multipart_completion.py tests/integration/test_m2_completion.py tests/integration/test_m2_concurrency.py tests/integration/test_m2_completion_runner.py -q`

Expected: 202 work survives disconnect; long completion stays leased; takeover fences stale runners;
conditional races converge; no mismatch is deleted/overwritten; loss/replay is idempotent.

```bash
git add robolake/application robolake/infrastructure apps/completion_runner docker-compose.yml tests
git commit -m "feat(m2): publish multipart blobs create-only"
```

### Task 7: Expose strict API/CLI, metrics, and safe abort

**Files:**
- Modify: `apps/api/schemas/transfers.py`
- Modify: `apps/api/routes/transfers.py`
- Modify: `apps/api/errors.py`
- Modify: `robolake/infrastructure/api_client.py`
- Modify: `robolake/infrastructure/settings.py`
- Modify: `apps/cli/main.py`
- Modify: `apps/cli/commands/datasets.py`
- Modify: `apps/cli/output.py`
- Test: `tests/unit/api/test_m2_transfers.py`
- Test: `tests/unit/cli/test_m2_commands.py`
- Test: `tests/unit/infrastructure/test_settings.py`
- Test: `tests/integration/test_m2_abort.py`

**Interfaces:**
- Produces: public endpoints/strict schemas described in the architecture, existing push UX, safe
  status/progress, and explicit session abort.
- Consumes: application use cases from Tasks 4/6.

- [ ] **Step 1: Write API contract and redaction tests**

Assert strict unknown-field rejection, UUID request/invocation fields, max window 16, no independent
provider-upload-ID field, UploadPart response receipt accepted only by confirm (never complete),
stable initiation/admission errors, and query-canary absence from logs. Assert upload mutations carry
owner/epoch, status requires no lease, Complete returns replayable 202, and the client treats each
capability URL as opaque.

- [ ] **Step 2: Implement strict schemas and routes**

Use these response shapes:

```python
class MultipartPartStatusResponse(StrictSchema):
    part_number: int
    size_bytes: int
    state: str

class PartCapabilityResponse(StrictSchema):
    part_number: int
    size_bytes: int
    request: PresignedRequestResponse

class AdmissionLeaseResponse(StrictSchema):
    owner_id: UUID
    epoch: int
    expires_at: datetime

class MultipartStatusResponse(StrictSchema):
    session_id: UUID
    session_generation: int
    state: str
    completion_reason: str | None
    completion_phase: str | None
    planned_part_count: int
    resolved_part_count: int
    resolved_part_bytes: int
    parts: Sequence[MultipartPartStatusResponse]
```

Add required `ConfirmPartRequest` fields for opaque `upload_response_etag` and the requested response
checksum; these are validated against provider listing before progress. Public status may paginate part
details while totals remain derived. It never returns provider upload ID or completion lease
coordinates. Upload lease owner/epoch are fencing values, not authorization; CLI renews them during
upload and holds none while polling `COMPLETING`.

- [ ] **Step 3: Implement CLI behavior and metrics**

Keep `robolake push SOURCE --dataset NAME`; add `--part-concurrency` defaulting to settings and
validated `1..16`. Render `newly_transferred_part_bytes`, `reused_provider_part_bytes`,
`reconciled_part_bytes`, `completed_object_bytes`, `verification_read_bytes`, and
`whole_object_verification_duration` separately, never as “wire bytes.” Enforce the count/byte sum
invariants. After 202, poll short status requests; Ctrl-C stops waiting but does not cancel server
completion. A rerun seeing `COMPLETING` resumes polling. Add `robolake upload abort SESSION_UUID`;
Ctrl-C never invokes it.

- [ ] **Step 4: Implement abort disagreement rows 15–16**

`CREATED` cancel becomes terminal `CANCELLED` without provider I/O. `INITIATING` abort returns
`MULTIPART_INITIATION_IN_PROGRESS`. With a durable upload ID and current upload fence, commit
ABORTING, wait for CLI workers, retry provider abort, and mark ABORTED only after absence. If final
appears, transition to COMPLETING, release upload lease, and queue runner reconciliation instead. A
stale owner cannot initiate/record abort.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/api/test_m2_transfers.py tests/unit/cli/test_m2_commands.py tests/unit/infrastructure/test_settings.py tests/integration/test_m2_abort.py -q
uv run robolake --help
```

Expected: exact API/CLI contracts pass; help describes part-level upload resume, async completion
polling, and safe cancel/abort.

```bash
git add apps robolake/infrastructure tests
git commit -m "feat(m2): expose multipart push and abort"
```

### Task 8: Prove the vertical slice and publish measured evidence

**Files:**
- Create: `scripts/demo-m2.sh`
- Create: `scripts/benchmark-m2.py`
- Create: `tests/integration/test_m2_vertical_slice.py`
- Modify: `docs/M2_POISONED_FINAL_KEY_RUNBOOK.md`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE_OVERVIEW.md`
- Modify: `docs/SECURITY_MODEL_SUMMARY.md`
- Modify: `docs/KNOWN_LIMITATIONS_V0.1.0.md` only if release/version policy explicitly requires it;
  otherwise create release-specific M2 notes later.
- Modify: M2 design docs only for implementation-confirmed deviations through a new ADR.

**Interfaces:**
- Produces: small CI vertical slice, manual >5 GB interruption/resume harness, and user/operator docs.
- Consumes: complete M2 application.

- [ ] **Step 1: Add the small CI tracer test**

Inject a low test-only selection threshold while retaining provider-valid 6 MiB non-final parts.
Push, interrupt after receipt-backed VERIFIED part numbers, rerun, assert those parts receive no second PUT,
allow the first admission lease to expire/reacquire, reach READY, pull through unchanged M1 flow,
and compare bytes. Also stop the waiting CLI after 202, prove the completion runner continues, and
poll to the same READY result.

- [ ] **Step 2: Add the manual profile with resource preflight**

The script refuses unless free disk is at least 20 GB and records machine/containers/commit. It
stream-generates 5,000,000,001 deterministic bytes, kills the CLI after selected parts, resumes,
checks part metrics, stops/restarts the polling CLI after 202 without stopping the server runner,
pulls, runs full SHA-256 and `cmp`, records wall/RSS, and scans logs for secret canaries. It reports
newly-transferred, reused, reconciled, and verification reads separately. It is never selected by
default pytest/CI.

- [ ] **Step 3: Run all quality gates**

```bash
uv sync --locked
docker compose down -v
docker compose up -d --wait
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run mypy robolake apps scripts
uv run pytest
scripts/demo-v01.sh
```

Expected: every check passes, M1's 237-test baseline remains green, and overall coverage is at least
87%. Record exact new totals rather than copying these baseline numbers.

Add an offline-deployment contract harness/checklist test: migration is blocked until M1 workflows
are terminal and all v0.1.0 processes are stopped; mixed v0.1.0/M2 serving is explicitly rejected.
Exercise rollback refusal with non-terminal or unarchived M2 rows, then prove an allowed downgrade
preserves READY Versions, AVAILABLE Blobs, and final object bytes.

- [ ] **Step 4: Run the manual profile only with safe resources**

Run: `scripts/demo-m2.sh`

Expected: READY, resume without retransmitting resolved parts, byte-identical pull, bounded RSS, no
capability leak. If resources are insufficient, record “not run” and the measured reason.

- [ ] **Step 5: Review frozen semantics and commit**

Diff manifest bytes, v0.1.0 M1 endpoint schemas, READY/AVAILABLE triggers, pull behavior, and M1 error
codes against tag `v0.1.0`. Any semantic difference stops implementation and requires a new ADR.
Prove direct SQL can enforce verification-evidence structure but not claim external-I/O proof; the
live provider test must independently observe the complete streamed read.
Exercise the runbook's read-only inspection queries against a poisoned synthetic final key; do not
automate its delete/adopt decisions in product code.

```bash
git add scripts tests README.md docs
git commit -m "docs(m2): add multipart evidence and guidance"
```

## Self-review checklist

- Every mandatory provider/failure-matrix row maps to a focused test above.
- Request, invocation, Blob-scoped generation, upload lease, and completion lease remain distinct in
  every interface; request/session identities are never rebound/reactivated.
- `CREATED`/`INITIATING` makes provider creation ambiguity explicit; abort never guesses an upload ID.
- `UploadPart` has no ISSUED state; capability metadata never advances progress.
- Complete uses retained UploadPart response ETags; ListParts-only observations never manufacture a
  receipt and response loss triggers safe exact-part retransmission.
- Completion is 202/durable/server-owned, releases upload admission, uses PostgreSQL rather than an
  external queue, heartbeats long I/O, and fences stale runner commits.
- 409/NoSuchUpload full restart cannot adopt any old provider part; guarded same-MPU recovery is
  limited to the documented non-409 partial case.
- Newly transferred, reused, and reconciled invocation metrics are disjoint and never claim wire
  bytes.
- Offline rollout/rollback never runs v0.1.0 beside active M2 rows and never removes final Blobs.
- Plan contains no temporary-object path, production byte proxy, pull change, final delete, repair,
  general GC, authentication, or excluded technology.
- Exact schema-v1 canonical part-plan bytes/hash have golden vectors and mutation tests.
- All signatures distinguish `UploadPartReceipt`, `ListedProviderPart`, `CompletedPartReceipt`,
  `HashedMultipartPlan`, and `PartCapabilityWindow` consistently.
- Implementation may split large modules after a review gate, but must preserve the listed public
  interfaces and import boundaries.
- No task proceeds to the next review gate with red tests, hidden warnings, or undocumented provider
  deviation.

## Implementation checkpoints

1. Protocol and schema review after Tasks 1–2.
2. Provider contract review after Task 3.
3. Resume tracer review after Tasks 4–5.
4. Final publication/integrity review after Task 6.
5. Public contract/security review after Task 7.
6. Full release-quality evidence after Task 8.

Execute this plan only in a separately authorized M2 implementation task; design approval does not
authorize production code or migration changes in this documentation PR.
