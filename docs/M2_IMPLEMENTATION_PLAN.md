# M2 Resumable Multipart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add resumable multipart push for one Blob above 5,000,000,000 bytes while preserving every
released M1 identity, publication, integrity, pull, and error invariant.

**Architecture:** The API creates one provider MPU directly at the immutable final SHA-256 key,
persists a deterministic part plan, reconciles ListParts, and issues a bounded rolling capability
window. Conditional completion publishes at most one object; a streamed whole-object SHA-256 is
mandatory before AVAILABLE. A separate expiring admission lease fences each active invocation while
the persistent session/provider MPU remains resumable.

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
  Blob is 50,000,000,000,000 bytes.
- Final Complete always carries `If-None-Match: *`; every new multipart final object is streamed and
  whole-file SHA-256 verified before AVAILABLE.
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
| `robolake/domain/multipart.py` | Pure part-size algorithm, frozen part values, state transitions. |
| `robolake/domain/constants.py` | M2 protocol constants only. |
| `robolake/domain/errors.py` | Stable M2 error classes/codes. |
| `robolake/domain/records.py` | Framework-free session/part records. |
| `robolake/application/contracts.py` | Part plans, status, capability window, invocation metrics. |
| `robolake/application/ports.py` | Registry/provider/local transfer interfaces. |
| `robolake/application/multipart.py` | Prepare, reconcile, issue, confirm, complete, abort use cases. |
| `robolake/application/admission.py` | Acquire/renew fenced multipart admission leases. |
| `robolake/application/push.py` | Select M1/M2 and orchestrate rolling-window resume. |
| `robolake/infrastructure/models.py` | SQLAlchemy session/part mappings. |
| `robolake/infrastructure/store.py` | Transactional session/part persistence and locks. |
| `robolake/infrastructure/object_storage.py` | MPU control, paginated ListParts, exact presigning, conditional Complete, abort. |
| `robolake/infrastructure/scanner.py` | One stable pass producing full and part hashes for large files. |
| `robolake/infrastructure/http_transfer.py` | Positional bounded UploadPart streaming/outcome only. |
| `robolake/infrastructure/api_client.py` | Strict M2 API response parsing without provider details. |
| `robolake/infrastructure/settings.py` | Operational concurrency/lease limits with startup validation. |
| `apps/api/schemas/transfers.py` | Strict multipart requests/responses. |
| `apps/api/routes/transfers.py` | Thin session endpoints. |
| `apps/api/errors.py` | Safe stable action mapping. |
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
  `MultipartSessionState`, `UploadPartState`, and M2 domain errors.
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
        (5 * 1024**4, 1_073_741_824, 5_120, 1_073_741_824),
        (50_000_000_000_000, 5_368_709_120, 9_314, 1_211_965_440),
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
    if not 5_000_000_000 < size_bytes <= 50_000_000_000_000:
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
`MULTIPART_COMPLETION_AMBIGUOUS`, and `FINAL_BLOB_PUBLICATION_CONFLICT`. Generate every enum pair
and assert only documented edges succeed. Include guarded `COMPLETING -> IN_PROGRESS`; prove 409
and `NoSuchUpload` with no final terminate the attempt. `UploadPartState` contains only `PENDING`,
`UPLOADED`, and `VERIFIED`; capability issuance is not a state.

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
- Test: `tests/unit/infrastructure/test_store.py`

**Interfaces:**
- Consumes: domain session/part states and `MultipartPlan` from Task 1.
- Produces: `UploadPartModel`, `MultipartAdmissionLeaseModel`, fenced repository operations,
  plan reconciliation, and guarded offline migration/downgrade.

- [ ] **Step 1: Write migration-from-M1 and direct-SQL failure tests**

Start from revision `20260711_0002`, insert a valid M1 SINGLE_PUT row, upgrade, and assert it remains
unchanged. Add SQL assertions that boundary mutation, part reparenting, VERIFIED without matching
provider checksum, COMPLETING with a missing part, an unguarded COMPLETING regression, and COMPLETED
without structurally consistent full-stream evidence all raise SQLSTATE `23514`. Prove different
part numbers may share expected digest, provider checksum, and ETag while duplicate part number and
overlapping ranges remain invalid.

Add lease tests for 64 abandoned/expired rows, atomic same-session takeover, monotonic epoch,
current-owner renewal, stale-owner mutation rejection, and database-time expiry.

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
    provider_etag: Mapped[str | None] = mapped_column(Text)
    provider_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
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
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    epoch: Mapped[int] = mapped_column(BigInteger)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

Add the immutable verification-evidence and same-MPU reconciliation fields defined in the migration
design. `UploadPart.state` allows only PENDING, UPLOADED, and VERIFIED; capability metadata is
diagnostic and does not advance state.

- [ ] **Step 4: Implement transactional repository methods**

Define these exact interfaces:

| Method | Signature |
| --- | --- |
| Create/resolve | `create_or_resolve_multipart(version_id: UUID, blob_id: UUID, plan: HashedMultipartPlan, idempotency_key: str) -> MultipartUploadContext` |
| Acquire/renew lease | `acquire_multipart_lease(session_id: UUID, owner_id: UUID, ttl_seconds: int, capacity: int) -> AdmissionLease` |
| Lock with fence | `lock_multipart_session(session_id: UUID, owner_id: UUID, epoch: int) -> MultipartUploadContext` |
| Provider acknowledgement | `record_provider_upload(session_id: UUID, owner_id: UUID, epoch: int, provider_upload_id: str) -> None` |
| Part reconciliation | `reconcile_parts(session_id: UUID, owner_id: UUID, epoch: int, provider_parts: Sequence[ProviderPart], attribution: PartAttribution) -> None` |
| Guarded recovery | `recover_partial_completion(session_id: UUID, owner_id: UUID, epoch: int, evidence: PartialCompletionEvidence) -> None` |
| State transition | `transition_multipart_session(session_id: UUID, owner_id: UUID, epoch: int, expected: MultipartSessionState, target: MultipartSessionState) -> None` |

Use `SELECT ... FOR UPDATE`, retry the active-session uniqueness race by selecting the winner, and
never persist counters derivable from rows. Lease acquisition uses one transaction-level advisory
lock and PostgreSQL time; every mutation revalidates owner/epoch/expiry. A provider call happens
outside the DB transaction and its result is fenced again before commit.

- [ ] **Step 5: Verify upgrade, constraints, downgrade guard, and M1 regression**

Run:

```bash
uv run pytest tests/integration/test_m2_migration.py tests/integration/test_m2_database_invariants.py tests/integration/test_m2_admission_leases.py tests/unit/infrastructure/test_store.py -q
uv run pytest tests/integration/test_database_constraints.py -q
```

Expected: all selected tests pass; 64 expired leases do not exhaust admission; downgrade refuses
while multipart session/part/lease rows exist and succeeds only after terminal workflow evidence is
archived/removed under the documented offline rollback.

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
- Produces: `MultipartObjectStore` methods below and `ProviderPart`/`CompleteOutcome` contracts.
- Consumes: exact bucket/key/session/part inputs; never domain-manages provider error bodies.

- [ ] **Step 1: Port the provider probes into failing integration tests**

Create isolated synthetic keys and test initiation, presigned exact headers, ListParts, replacement,
checksum rejection, two different part numbers with identical bytes/ETag/checksum, conditional
200/412 race, lost/repeated completion, Abort/NoSuchUpload, and no log leakage. Feed a fake HTTP 200
response with an embedded `<Error>` element through the adapter and assert it returns failure rather
than `CompleteOutcome.CREATED`. Preserve the negative
unguarded-overwrite proof as a probe test that never runs against non-ephemeral buckets.

- [ ] **Step 2: Define the port**

| Method | Exact signature |
| --- | --- |
| Initiate | `create_multipart(object_key: str) -> str` |
| List | `list_parts(object_key: str, provider_upload_id: str) -> Sequence[ProviderPart]` |
| Presign | `presign_part(object_key: str, provider_upload_id: str, part_number: int, size_bytes: int, checksum_base64: str, expires_seconds: int) -> PresignedRequest` |
| Complete | `complete_multipart(object_key: str, provider_upload_id: str, parts: Sequence[ProviderPart]) -> CompleteOutcome` |
| Abort | `abort_multipart(object_key: str, provider_upload_id: str) -> None` |

`list_parts` must follow every `IsTruncated` marker. `complete_multipart` always passes
`IfNoneMatch="*"`, requires an SDK/adapter parsed success result rather than status 200 alone, and
translates embedded error/412/409/NoSuchUpload into typed outcomes. It never returns a provider body.

- [ ] **Step 3: Run red tests**

Run: `uv run pytest tests/unit/infrastructure/test_object_storage.py tests/integration/test_m2_provider_contract.py -q`

Expected: new tests fail because the port/adapter methods do not exist.

- [ ] **Step 4: Implement minimal adapter and policy**

Use `ChecksumAlgorithm="SHA256"` at initiation; sign exact part length/checksum; capture ETag only as
opaque completion input; parse provider SHA-256 into canonical hex. Keep existing final prefix and
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
- Produces: `MultipartTransferService.prepare`, `status`, `reconcile`, `issue_capabilities`, and
  `confirm_part`, plus admission acquire/renew/takeover.
- Consumes: repository from Task 2 and storage port from Task 3.

- [ ] **Step 1: Write disagreement and lease tests**

Use fakes where DB/provider parts differ. Assert provider matching parts become VERIFIED/reused,
absence resets PENDING, mismatch schedules exact replacement, active session race resolves one row,
and create-response loss leaves no guessed upload ID. Add the failure-matrix lease rows: 64 expired
leases admit new work, one reacquisition winner increments epoch, stale owner mutation fails, and a
late exact provider part is reconciled by the new owner.

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
| Prepare | `prepare(version_id: UUID, blob_sha256: Sha256Digest, part_checksums: Sequence[Sha256Digest], idempotency_key: str) -> MultipartPreparation` |
| Acquire/renew | `acquire_lease(session_id: UUID, owner_id: UUID) -> AdmissionLease` |
| Reconcile | `reconcile(session_id: UUID, owner_id: UUID, epoch: int, phase: ReconciliationPhase) -> MultipartStatus` |
| Issue | `issue_capabilities(session_id: UUID, owner_id: UUID, epoch: int, part_numbers: Sequence[int], limit: int) -> PartCapabilityWindow` |
| Confirm | `confirm_part(session_id: UUID, owner_id: UUID, epoch: int, part_number: int, outcome: UploadPartOutcome) -> MultipartStatus` |

Server recomputes the plan from Blob size and requires exactly one checksum per part. Prepare first
returns an AVAILABLE Blob unchanged, then resolves/creates the active session. Confirm ignores any
client ETag/checksum and calls provider reconciliation. Initial matches are attributed as reused;
unambiguous current-invocation successes become newly transferred after verification; ambiguous or
late/concurrent discoveries become reconciled. Capability issuance updates diagnostics only.

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
scheduling, in-flight workers settle, and the complete window is reconciled.

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

For each unique Blob: use the existing M1 workflow at `<= 5_000_000_000`; otherwise run prepare,
acquire/renew the admission lease, reconcile, run rolling upload, and confirm with the current
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

### Task 6: Implement conditional completion and whole-byte publication verification

**Files:**
- Modify: `robolake/application/multipart.py`
- Modify: `robolake/infrastructure/store.py`
- Modify: `robolake/infrastructure/object_storage.py`
- Test: `tests/unit/application/test_multipart_completion.py`
- Test: `tests/integration/test_m2_completion.py`
- Test: `tests/integration/test_m2_concurrency.py`

**Interfaces:**
- Produces: `complete(session_id, owner_id, epoch, idempotency_key) -> MultipartPreparation` and the
  only M2 path to `Blob AVAILABLE`.
- Consumes: reconciled provider parts, conditional completion outcomes, streamed `iter_bytes`.

- [ ] **Step 1: Write rows 5–13 and 17–18 as failing tests**

Cover provider-success/API-loss, provider-response loss, embedded error inside HTTP 200, repeated
Complete, matching/mismatching final, two sessions, one publisher while another uploads, and 412.
Explicitly test all four recovery cases: matching final; final absent with same MPU/all parts;
final absent with same MPU/valid subset taking guarded COMPLETING regression; and 409 or
`NoSuchUpload` with no final creating a new all-PENDING session/ID. Inject a final object whose
composite checksum matches the part plan but whole SHA differs; assert it never becomes AVAILABLE.
Expire/take over the lease around the provider call and prove a stale result cannot commit.

- [ ] **Step 2: Verify red state**

Run: `uv run pytest tests/unit/application/test_multipart_completion.py tests/integration/test_m2_completion.py tests/integration/test_m2_concurrency.py -q`

Expected: failures show no COMPLETING/final verification orchestration exists.

- [ ] **Step 3: Implement completion decision order**

```python
def complete(
    self, session_id: UUID, owner_id: UUID, epoch: int, idempotency_key: str
) -> MultipartPreparation:
    context = self.registry.lock_multipart_session(session_id, owner_id, epoch)
    if context.blob.state is BlobState.AVAILABLE:
        return self._available(context)
    self._reconcile_all(context)
    self._require_all_verified(context)
    self.registry.mark_completing(context.session.id, owner_id, epoch)
    outcome = self.objects.complete_multipart(
        context.blob.object_key, context.session.provider_upload_id, context.provider_parts
    )
    return self._reconcile_final(context, owner_id, epoch, outcome)
```

`_reconcile_final` checks AVAILABLE, HEAD/size, streams `iter_bytes` into SHA-256, and only then
records immutable `FULL_STREAM_SHA256` observed digest/size/read bytes/time/verifier and commits Blob
AVAILABLE + session COMPLETED under a revalidated fence. PostgreSQL checks evidence structure but
the integration test proves the actual provider read. On partial same-MPU non-409 outcome, take only
the guarded recovery edge. On 409 or `NoSuchUpload` with absent final, terminate the attempt,
release the lease, and create a new MPU/session with no adopted receipts. On 412, verify the existing
final before success. On mismatch, record the stable cause and never call delete/put/complete again.

- [ ] **Step 4: Add explicit proof that final verification reads every byte once**

The fake iterator records offsets/chunks; the live test corrupts one byte and asserts exit path 5.
Assert an already AVAILABLE Blob performs zero provider reads and a new multipart Blob performs one
complete read.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/unit/application/test_multipart_completion.py tests/integration/test_m2_completion.py tests/integration/test_m2_concurrency.py -q`

Expected: conditional races converge; no mismatch is deleted/overwritten; loss/replay is idempotent.

```bash
git add robolake/application robolake/infrastructure tests
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

Assert strict unknown-field rejection, max window 16, no independent provider-upload-ID response
field, no client ETag on confirm/complete, stable codes/actions including `ADMISSION_LEASE_LOST`,
and query-canary absence from TestClient/CLI captured logs. Assert every mutating route requires the
current owner/epoch and the client treats each capability URL as opaque.

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
    state: str
    planned_part_count: int
    resolved_part_count: int
    resolved_part_bytes: int
    parts: Sequence[MultipartPartStatusResponse]
```

The public status may paginate part details while totals remain derived. The API endpoint never
returns the provider upload ID. Lease owner/epoch are fencing coordinates, not secrets or
authorization; the CLI renews them on the configured heartbeat and stops mutations after loss.

- [ ] **Step 3: Implement CLI behavior and metrics**

Keep `robolake push SOURCE --dataset NAME`; add `--part-concurrency` defaulting to settings and
validated `1..16`. Render `newly_transferred_part_bytes`, `reused_provider_part_bytes`,
`reconciled_part_bytes`, `completed_object_bytes`, `verification_read_bytes`, and
`whole_object_verification_duration` separately, never as “wire bytes.” Enforce the count/byte sum
invariants. Add `robolake upload abort SESSION_UUID`; Ctrl-C never invokes it.

- [ ] **Step 4: Implement abort disagreement rows 15–16**

Under the current lease fence, commit ABORTING before provider call, wait for worker cancellation at
the CLI, retry abort as needed, and mark ABORTED only after `NoSuchUpload`. If final appears, run
final verification instead. A stale lease owner cannot initiate or record abort.

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/unit/api/test_m2_transfers.py tests/unit/cli/test_m2_commands.py tests/unit/infrastructure/test_settings.py tests/integration/test_m2_abort.py -q
uv run robolake --help
```

Expected: exact API/CLI contracts pass; help describes file-level multipart resume and safe abort.

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
Push, interrupt after known part numbers, rerun, assert those provider parts receive no second PUT,
allow the first admission lease to expire/reacquire, reach READY, pull through unchanged M1 flow,
and compare bytes.

- [ ] **Step 2: Add the manual profile with resource preflight**

The script refuses unless free disk is at least 20 GB and records machine/containers/commit. It
stream-generates 5,000,000,001 deterministic bytes, kills the CLI after selected parts, resumes,
checks part metrics, pulls, runs full SHA-256 and `cmp`, records wall/RSS, and scans logs for secret
canaries. It reports newly-transferred, reused, and reconciled part payload separately. It is never
selected by default pytest/CI.

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
- Persistent session state and expiring admission/fencing state remain separate in every interface.
- `UploadPart` has no ISSUED state; capability metadata never advances progress.
- 409/NoSuchUpload full restart cannot adopt any old provider part; guarded same-MPU recovery is
  limited to the documented non-409 partial case.
- Newly transferred, reused, and reconciled invocation metrics are disjoint and never claim wire
  bytes.
- Offline rollout/rollback never runs v0.1.0 beside active M2 rows and never removes final Blobs.
- Plan contains no temporary-object path, production byte proxy, pull change, final delete, repair,
  general GC, authentication, or excluded technology.
- All signatures use `MultipartSessionState`, `HashedMultipartPlan`, `ProviderPart`, and
  `PartCapabilityWindow` consistently.
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
