# M2 Migration and Rollback

## Migration objective

M2 adds generation-numbered resumable multipart attempts, temporary upload/completion leases, and a
PostgreSQL completion work registry without changing v0.1.0 manifests, DatasetVersion rows, Blob
identity, READY/AVAILABLE meaning, or pull data. Existing M1 `SINGLE_PUT` sessions remain valid and
need no rewrite.

The proposed Alembic revision follows `20260711_0002` and is additive before constraints are
replaced. Exact revision identifiers are selected during implementation, not this design task.

## `upload_sessions` extension

Add nullable multipart fields:

| Column | Type | Rule |
| --- | --- | --- |
| `session_generation` | `integer` | Required and positive for multipart; immutable; unique with `blob_id`; allocated under a Blob lock. Null for historical M1 rows. |
| `provider_upload_id` | `text` | Null in `CREATED`/`INITIATING`/`CANCELLED`; required after multipart reaches `IN_PROGRESS`; opaque, immutable once stored, session-scoped with final key, and never independently exposed. It is not a RoboLake identity or uniqueness key. |
| `part_size_bytes` | `bigint` | Required for multipart; 64 MiB..5 GiB; immutable from committed `CREATED` registration. |
| `planned_part_count` | `integer` | Required for multipart; 1..10,000; equals the protocol formula over Blob size. |
| `part_plan_schema_version` | `smallint` | Exactly 1 for M2. |
| `part_plan_sha256` | `char(64)` | SHA-256 of the exact schema-v1 canonical JSON bytes defined below; freeze evidence, not Blob identity. |
| `initiation_started_at` | `timestamptz` | Required in/after `INITIATING`; records intent before CreateMultipartUpload. |
| `initiation_ambiguous_at` | `timestamptz` | Required for terminal `INITIATION_AMBIGUOUS`; no upload ID is claimed. |
| `completion_requested_at` | `timestamptz` | Required in/after `COMPLETING`; makes the PostgreSQL row durable pending work. |
| `completion_reason` | `varchar(32)` | `PARTS_READY` or `FINAL_PRESENT`; immutable while one accepted work item remains `COMPLETING`. A later legal re-entry after guarded requeue may replace it. It selects the structural entry gate, not integrity truth. |
| `completion_phase` | `varchar(32)` | Nullable outside `COMPLETING`; while completing exactly `PENDING`, `ASSEMBLING`, `FINAL_PRESENT`, or `FINAL_VERIFICATION`. This is session work progress, not Blob integrity state or byte progress. |
| `completion_attempted_at` | `timestamptz` | Runner observation of the latest provider Complete attempt; not success proof. |
| `last_completion_result` | `varchar(32)` | Nullable application observation such as `AMBIGUOUS`, `CONFLICT_409`, or `EMBEDDED_ERROR`; never integrity proof. |
| `last_provider_reconciled_at` | `timestamptz` | Application-recorded time of the latest complete paginated `ListParts`. |
| `final_absence_observed_at` | `timestamptz` | Application-recorded HEAD observation used only by the guarded same-MPU recovery edge. |
| `provider_attempt_invalidated_at` | `timestamptz` | Required when 409 or `NoSuchUpload` with no final makes the provider attempt terminal. |
| `verification_method` | `varchar(32)` | For M2 completion, exactly `FULL_STREAM_SHA256`. |
| `observed_sha256` | `char(64)` | Application-computed digest from the final provider stream. |
| `observed_size_bytes` | `bigint` | Final object size observed by the verifier. |
| `verification_read_bytes` | `bigint` | Bytes consumed by the successful evidence-producing read; must equal Blob size. Partial failed attempts are not durable progress. |
| `verification_started_at` | `timestamptz` | Optional diagnostic start time. |
| `verification_completed_at` | `timestamptz` | Required for multipart `COMPLETED`. |
| `verifier_implementation` | `varchar(128)` | Bounded implementation/version identifier for reproducibility. |
| `abort_requested_at` | `timestamptz` | Required in `ABORTING`/`ABORTED`. |

Do not duplicate `final_object_key`: `upload_sessions.blob_id -> blobs.object_key` is the only final
key source. Do not add `temporary_object_key`: selected Option A has no temporary object.

Replace checks while preserving M1 values:

```text
strategy IN ('SINGLE_PUT', 'MULTIPART')
state IN ('CREATED', 'INITIATING', 'IN_PROGRESS', 'COMPLETING',
          'COMPLETED', 'ABORTING', 'ABORTED', 'CANCELLED', 'FAILED')
```

Strategy-specific checks require M2-only fields null for `SINGLE_PUT`. For `MULTIPART`, plan fields
are non-null, Blob size is in `5_000_000_001..5_000_000_000_000`, and provider-ID requirements
follow state. Existing M1 transitions remain legal.
Multipart transitions follow [the state-machine document](M2_STATE_MACHINES.md).

Add `UNIQUE (blob_id, session_generation)` for multipart identity. Rebuild
`uq_upload_sessions_active_blob` for `CREATED`, `INITIATING`, `IN_PROGRESS`, `COMPLETING`, and
`ABORTING`. This enforces one active generation per Blob, not either execution cap. An abandoned
active session remains resolvable after its upload or completion lease expires. Terminal generations
are immutable and never reactivated.

## Request and invocation identity

Reuse `idempotency_records` with three M2 scopes:

- `multipart-session-resolve`: `key` is canonical UUID `request_id`, request SHA-256 covers the typed
  create/resolve body, and `resource_id` permanently identifies the resolved session generation;
- `multipart-admission-acquire`: `key` is canonical UUID acquire `request_id`, request SHA-256 covers
  session plus `invocation_id`, and `response_json` permanently records owner/epoch/expiry result;
- `multipart-completion-accept`: `key` is the completion request UUID, request SHA-256 covers the
  session, command schema, original fence coordinates, and every semantic completion field; the
  response permanently records the canonical 202 acceptance or an already-terminal idempotent
  result without a capability or provider upload ID.

M2 scope rows are immutable after their complete response is inserted. Reuse with another canonical
request digest returns `IDEMPOTENCY_CONFLICT`; no terminal session or old request record is rebound.
`invocation_id` is a separate UUID for one CLI run and is stored on the admission lease for metrics
and diagnosis. Replaying an expired acquire request returns its original expired result; reacquisition
uses a new request UUID. Existing M1 idempotency keys/scopes remain unchanged.

Completion lookup precedes admission fencing. An existing matching hash returns the stored response
without reading the lease/session for action; a mismatching hash returns `IDEMPOTENCY_CONFLICT`.
Only an absent record reaches first-execution fence and Part validation. In one transaction, first
execution transitions the session to `COMPLETING`, sets `completion_phase=PENDING`, creates pending
completion work, releases the upload lease, and inserts the immutable 202 record. The scope/key
unique constraint makes simultaneous first executions converge: the loser rolls back, rereads, and
replays the committed record. This transaction boundary is required for response loss, process death
after commit, lease release/expiry, and completion-runner takeover.

A scope-aware constraint/trigger validates canonical UUID keys and the expected resource type for M2
records, then rejects update/delete after insertion. It must not tighten or reinterpret existing M1
scopes. Direct-SQL tests prove a request cannot be rebound to another resource or response.

## Canonical part-plan digest

The server hashes its own UTF-8 encoding of this exact field order:

```json
{"schema_version":1,"blob_size_bytes":12582912,"part_size_bytes":6291456,"part_count":2,"parts":[{"part_number":1,"offset_bytes":0,"size_bytes":6291456,"sha256":"1111111111111111111111111111111111111111111111111111111111111111"},{"part_number":2,"offset_bytes":6291456,"size_bytes":6291456,"sha256":"2222222222222222222222222222222222222222222222222222222222222222"}]}
```

Use compact separators, no whitespace/trailing newline, JSON integers only, ascending part number,
and lowercase 64-character digests. Unknown/duplicate fields and non-canonical alternatives are
invalid. The example is a compact provider-contract fixture, not the M2 size-selection threshold.

## New `upload_parts` table

```text
upload_parts
  upload_session_id        uuid        FK upload_sessions(id) ON DELETE RESTRICT
  part_number              integer
  offset_bytes             bigint
  size_bytes               bigint
  sha256                   char(64)
  state                    varchar(16)  PENDING|UPLOADED|VERIFIED
  upload_response_etag     text null
  upload_response_checksum_sha256_base64 varchar(44) null
  upload_response_received_at timestamptz null
  listed_etag              text null
  listed_checksum_sha256_base64 varchar(44) null
  listed_size_bytes        bigint null
  provider_listed_at       timestamptz null
  last_capability_issued_at timestamptz null
  last_capability_expires_at timestamptz null
  capability_issue_count   integer not null default 0
  uploaded_at              timestamptz null
  verified_at              timestamptz null
  last_error_code          varchar(64) null
  created_at               timestamptz

PRIMARY KEY (upload_session_id, part_number)
UNIQUE (upload_session_id, offset_bytes)
```

Checks:

- part number `1..10_000`, offset `>= 0`, size `> 0`, canonical lowercase SHA-256;
- `UPLOADED` requires `upload_response_etag`, canonical padded RFC 4648
  `upload_response_checksum_sha256_base64`, `upload_response_received_at`, and `uploaded_at`;
- `VERIFIED` requires the stored UploadPart response ETag, current listed ETag equal to that response
  ETag, listed size equal to expected size, listed SHA-256 equal to expected SHA-256, and
  `provider_listed_at`/`verified_at`; decoding response/listed Base64 must yield exactly 32 bytes and
  equal the bytes decoded from expected lowercase hexadecimal `sha256`;
- each persisted provider checksum matches `^[A-Za-z0-9+/]{43}=$` and round-trips through PostgreSQL
  `decode(value, 'base64')`/`encode(..., 'base64')`, preventing non-canonical encodings;
- capability timestamps/count are optional diagnostic metadata and never part state or progress;
- receipt/checksum lengths are bounded; ETag is never an integrity predicate by itself;
- session must use `MULTIPART` strategy; and
- no mutable completed/progress counters are stored.

Different part numbers may contain identical bytes and therefore may have identical expected
SHA-256 values, response/listed checksums, and response/listed ETags. None is unique. Uniqueness
applies only to `(upload_session_id, part_number)` and the immutable non-overlapping range plan.

The complete receipt is
`(part_number, upload_response_etag, upload_response_checksum_sha256_base64)` from one successful
UploadPart response. Expected part SHA-256 remains lowercase hexadecimal. Provider request/response
`ChecksumSHA256` is the canonical padded Base64 encoding of those same 32 raw bytes; deterministic
conversion is `base64.b64encode(bytes.fromhex(sha256)).decode("ascii")`. A multipart composite
checksum is not accepted as either this per-part digest or canonical whole-Blob SHA-256.

ListParts is current-provider evidence, not the source of a complete receipt. A matching listed part
with either response receipt field missing remains `PENDING` and must be re-uploaded exactly to
capture a successful response. Complete sends `PartNumber`, `upload_response_etag`, and
`upload_response_checksum_sha256_base64`, ordered by part number. AWS makes checksum fields optional
in the general `CompletedPart` model; their presence is a deliberate RoboLake SHA-256 profile rule.

## New `multipart_admission_leases` table

```text
multipart_admission_leases
  upload_session_id uuid        PRIMARY KEY FK upload_sessions(id) ON DELETE CASCADE
  invocation_id     uuid        NOT NULL
  owner_id          uuid        NOT NULL
  epoch             bigint      NOT NULL CHECK (epoch > 0)
  acquired_at       timestamptz NOT NULL
  renewed_at        timestamptz NOT NULL
  expires_at        timestamptz NOT NULL
```

The server generates `owner_id`; callers provide an idempotent acquire request UUID and their
`invocation_id`. The row persists across expiry so takeover can atomically increment `epoch`. Under a fixed
PostgreSQL transaction-level advisory lock, acquisition:

1. uses database time, not caller time;
2. renews the same unexpired owner without changing epoch;
3. rejects a different owner while the lease is unexpired;
4. counts only rows with `expires_at > transaction_timestamp()` against
   `MAX_ACTIVE_MULTIPART_LEASES`;
5. inserts epoch 1 or takes over an expired row with `epoch + 1`; and
6. returns the current owner/epoch for fencing.

The lease TTL and capacity are operational configuration. Lease expiry never mutates
`UploadSession`, aborts the provider MPU, clears part receipts, or creates an `EXPIRED` session.
Every upload-phase mutating repository method carries owner/epoch and executes an atomic SQL
predicate that also requires unexpired database time. Zero affected rows maps to
`ADMISSION_LEASE_LOST`. Status reads do not require a lease. Structural database triggers do not
claim to prove caller lease ownership; privileged operator SQL is outside the application fence.

“Release” or “end” retains the lease row and its fencing history but sets `expires_at` to database
time (and updates `renewed_at`) in the owning transaction. It does not delete the row or reset epoch.
A later guarded recovery reacquires by incrementing epoch, so stale coordinates can never become
current again.

The normal `INITIATING -> FAILED(INITIATION_AMBIGUOUS)` repository operation requires the current
owner/epoch and performs the terminal session update plus admission-lease release in one transaction.
A stale owner changes neither row. If the process dies before terminalization, database-time lease
expiry is the bounded fallback. Consequently, terminalizing 64 ambiguous initiations immediately
returns all 64 admission slots without waiting for TTL.

## New `multipart_completion_leases` table

```text
multipart_completion_leases
  upload_session_id uuid        PRIMARY KEY FK upload_sessions(id) ON DELETE CASCADE
  owner_instance_id uuid        NOT NULL
  epoch             bigint      NOT NULL CHECK (epoch > 0)
  acquired_at       timestamptz NOT NULL
  renewed_at        timestamptz NOT NULL
  expires_at        timestamptz NOT NULL
```

`COMPLETING` plus `completion_requested_at` is the durable work record. A bounded server runner using
the same application artifact atomically claims eligible rows with `FOR UPDATE SKIP LOCKED` or an
equivalent compare-and-set, then inserts/takes over this lease. Upload admission leases do not count
toward completion capacity and are released when completion is accepted. Completion concurrency,
lease TTL, and heartbeat interval are operational settings; defaults are 2, 120 seconds, and 30
seconds, with hard concurrency maximum 8 and heartbeat at most TTL/3.

Every completion phase/evidence/terminal write requires a matching unexpired completion owner/epoch
in one atomic repository statement. Heartbeats use short independent transactions while provider
Complete or full GET is in flight. Lease expiry does not change session state; a new runner
reconciles and restarts whole-object verification from byte zero if needed.

The Blob remains `UPLOADING` during `ASSEMBLING`, `FINAL_PRESENT`, and `FINAL_VERIFICATION`. A
successful complete read is published in one short fenced transaction that writes immutable
evidence, transitions Blob `UPLOADING -> VERIFYING -> AVAILABLE`, sets session `COMPLETED`, and ends
completion ownership. Proven mismatch similarly writes mismatch evidence and transitions Blob
`UPLOADING -> VERIFYING -> FAILED` plus terminal session failure. Transient provider/read failure
commits neither evidence nor a Blob transition.

## Deferred constraint triggers

Use PostgreSQL constraint triggers so one transaction can insert a complete plan before sealing it.

1. **Plan completeness:** before `CREATED -> INITIATING`, rows are consecutive `1..N`, offsets are
   exact, non-final sizes equal frozen part size, final size is `1..part_size`, and summed bytes equal
   immutable Blob size within the portable M2 maximum. Recomputed canonical schema-v1 bytes must
   equal `part_plan_sha256`.
2. **Plan immutability:** session plan fields are immutable from insertion, and part
   number/offset/size/SHA rows cannot update, delete, or be added after the transaction that creates
   the complete `CREATED` plan commits. State and provider observations may change only through
   legal edges.
3. **Session identity:** Blob, initiating Version, strategy, generation, provider upload ID after first
   non-null, schema version, and plan digest are immutable. A part cannot move to another session.
4. **Initiation gate:** `CREATED -> INITIATING` requires complete plan and
   `initiation_started_at`; `INITIATING -> IN_PROGRESS` requires one durable provider upload ID;
   `INITIATING -> FAILED` with `INITIATION_AMBIGUOUS` requires `initiation_ambiguous_at` and never
   invents an upload ID. The application repository must atomically fence this terminal transition
   and release admission; a trigger can enforce row structure but not caller ownership.
   `CREATED -> CANCELLED` is the only no-provider cancellation edge.
5. **Completing gate:** entering `COMPLETING` requires `completion_requested_at`/`PENDING` and
   one reason that stays immutable until guarded exit or terminal completion. A later legal re-entry
   may replace it. `PARTS_READY` requires all `N` rows `VERIFIED`, a non-null stored UploadPart
   response ETag/Base64 checksum, equal latest listed ETag/Base64 checksum, and exact listed
   size/checksum on every part. Ordered Complete input contains PartNumber plus both stored response
   receipt fields.
   `FINAL_PRESENT` requires a freshly application-recorded final-key existence observation and is
   allowed from `IN_PROGRESS` or `ABORTING` without pretending incomplete parts are verified.
   Receipts and digests need not be distinct.
6. **Guarded recovery gate:** `COMPLETING -> IN_PROGRESS` requires unchanged provider upload ID,
   `final_absence_observed_at`, a fresh `last_provider_reconciled_at`, a non-409 completion result,
   a structurally valid set of receipt-backed matching parts. For ordinary completion ambiguity at
   least one part is unresolved; a vanished `FINAL_PRESENT` observation may requeue even a fully
   resolved set so the next accepted work uses `PARTS_READY`. Matching rows stay `VERIFIED`;
   absent/mismatching/receipt-less rows become `PENDING` in the same transaction and the completion
   lease is released.
7. **Attempt invalidation gate:** 409 or `NoSuchUpload` with no final requires
   `provider_attempt_invalidated_at` and a terminal `FAILED` attempt before another active session
   can be created. A new request allocates the next generation with new rows and no adopted receipt.
8. **Completed gate:** multipart `COMPLETED` requires Blob `AVAILABLE` and immutable verification
   evidence satisfying:
   `verification_method = 'FULL_STREAM_SHA256'`, `observed_sha256 = blobs.sha256`,
   `observed_size_bytes = blobs.size_bytes`, and `verification_read_bytes = blobs.size_bytes`.
   Every part must be VERIFIED only for `PARTS_READY`; `FINAL_PRESENT` may adopt a matching final
   object without falsely resolving this session's incomplete parts.
9. **Blob publication gate:** the Blob remains `UPLOADING` until complete external evidence exists.
   An associated locked multipart session must carry structurally consistent evidence for the short
   `UPLOADING -> VERIFYING -> AVAILABLE|FAILED` transaction. Existing M1 availability paths remain
   legal.
10. **Lease structure:** upload and completion lease rows require positive epochs and sane database
    timestamps. Caller/runner ownership is enforced by atomic repository SQL predicates, not by a
    trigger that claims knowledge of the external caller.
11. **Terminal guards:** `COMPLETED`, `ABORTED`, `CANCELLED`, and `FAILED` session rows cannot mutate. AVAILABLE
    Blob and READY Version guards remain unchanged.

PostgreSQL can enforce only the structure, equality, immutability, and transition consistency of
application-recorded evidence. It cannot independently prove that the application performed an
external GET or computed SHA-256 honestly. Integration tests must exercise the real provider read;
direct-SQL tests prove only that missing or inconsistent evidence cannot pass the structural gate.

## Offline upgrade order

The first M2 release does not support rolling mixed-version deployment:

1. disable all new push admission;
2. wait for or explicitly stop every non-terminal M1 transfer workflow;
3. stop every v0.1.0 API/server process;
4. verify no v0.1.0 process or database connection remains;
5. back up PostgreSQL and record the current Alembic head;
6. apply the additive M2 migration and install its constraints/triggers;
7. deploy only the M2-capable API and completion-runner processes from the same versioned artifact;
8. run migration, M1 regression, DB/provider contract, runner-claim, and health smoke tests;
9. enable multipart admission; and
10. monitor before restoring normal push traffic.

No v0.1.0 server may process requests once M2 rows or states can be created. Old binaries do not
understand M2 strategy/state values and may issue a whole-Blob single-PUT capability for a multipart
row. A mixed v0.1.0/M2 rolling deployment is explicitly unsupported; a future compatibility release
may define a two-phase rollout.

The migration acquires locks on `upload_sessions`; Dataset/Blob/Version tables receive only a
trigger replacement, not data rewriting.

## Application rollback

Prefer roll-forward. If rollback is unavoidable:

1. disable new push admission and stop every M2 server;
2. if reconciliation is necessary, start one isolated M2 API plus completion runner with new
   admission disabled, then stop both before downgrade;
3. block rollback while any non-terminal multipart session exists;
4. reconcile/complete or explicitly abort known attempts and prove no known provider MPU remains;
5. export terminal multipart session, part, both lease types, M2 request bindings, and verification
   diagnostics to an approved archive;
6. in one reviewed transaction, delete only terminal M2 workflow/lease/part rows required for schema
   downgrade; never delete DatasetVersion, DatasetEntry, Blob, or object data;
7. verify all `MULTIPART` session, `upload_parts`, upload/completion lease, and M2-scoped request-record
   counts are zero;
8. run the guarded Alembic downgrade and confirm the v0.1.0 schema contract; and
9. only then deploy v0.1.0 and re-enable M1 admission.

`READY` DatasetVersions and `AVAILABLE` Blobs created through M2 remain valid and pull-compatible
because their identity and publication semantics are unchanged. Rollback must never overwrite or
delete an AVAILABLE object. If terminal workflow history cannot be safely archived/removed, or any
downgrade precondition is uncertain, do not downgrade: restore the PostgreSQL backup or roll forward.

## Schema downgrade guard

Alembic downgrade fails closed if any `MULTIPART` session, `upload_parts` row, upload/completion lease
row, or M2-scoped request binding exists. It may drop M2 tables/columns and restore original
checks/index/trigger only when every count is zero. Silently discarding active or historical
multipart state is forbidden.

Database downgrade never removes object-store parts. Before a permitted downgrade, explicitly abort
known incomplete provider uploads and confirm absence; unknown create-response-loss MPUs remain
bounded by provider stale cleanup.

## Required migration and deployment evidence

- clean v0.1.0-to-M2 migration with existing M1 rows;
- offline gate refuses to proceed while an M1 process/workflow remains;
- negative compatibility exercise documents that v0.1.0 against active M2 rows is unsupported and
  is blocked by deployment orchestration, not falsely claimed as binary-compatible;
- 64 expired lease rows do not consume admission capacity;
- concurrent lease reacquisition produces one owner and monotonically increasing epoch;
- request ID replay returns one immutable binding, concurrent resolve produces one active generation,
  and a terminal attempt requires the next generation;
- `CREATED`, `INITIATING`, response-loss ambiguity, and cancellation/abort gates are exercised;
- two completion runners claim one row once, heartbeat a long verification, fence a stale runner, and
  safely take over after expiry;
- a CLI/API timeout or disconnect leaves accepted `COMPLETING` work running and status observable;
- direct SQL cannot complete without structurally valid verification evidence;
- actual provider streamed verification is proven separately by integration tests;
- downgrade refuses non-terminal or unarchived M2 workflow/request/lease rows; and
- rollback preserves READY Versions, AVAILABLE Blobs, and final objects byte-for-byte.
