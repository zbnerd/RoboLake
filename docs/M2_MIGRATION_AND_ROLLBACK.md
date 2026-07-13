# M2 Migration and Rollback

## Migration objective

M2 adds resumable multipart attempts and temporary admission leases without changing v0.1.0
manifests, DatasetVersion rows, Blob identity, READY/AVAILABLE meaning, or pull data. Existing M1
`SINGLE_PUT` sessions remain valid and need no rewrite.

The proposed Alembic revision follows `20260711_0002` and is additive before constraints are
replaced. Exact revision identifiers are selected during implementation, not this design task.

## `upload_sessions` extension

Add nullable multipart fields:

| Column | Type | Rule |
| --- | --- | --- |
| `provider_upload_id` | `text` | Required in multipart `IN_PROGRESS`/`COMPLETING`/`ABORTING`; opaque and never exposed as an independent stable RoboLake API field. Partial unique when non-null. |
| `part_size_bytes` | `bigint` | Required for multipart; 64 MiB..5 GiB; frozen after provider initiation. |
| `planned_part_count` | `integer` | Required for multipart; 1..10,000; equals the protocol formula over Blob size. |
| `part_plan_sha256` | `char(64)` | SHA-256 of canonical ordered `(number, offset, size, digest)` plan; idempotency/freeze evidence. |
| `completion_attempted_at` | `timestamptz` | Required before/while `COMPLETING`; records intent, not success. |
| `last_completion_result` | `varchar(32)` | Nullable application observation such as `AMBIGUOUS`, `CONFLICT_409`, or `EMBEDDED_ERROR`; never integrity proof. |
| `last_provider_reconciled_at` | `timestamptz` | Application-recorded time of the latest complete paginated `ListParts`. |
| `final_absence_observed_at` | `timestamptz` | Application-recorded HEAD observation used only by the guarded same-MPU recovery edge. |
| `provider_attempt_invalidated_at` | `timestamptz` | Required when 409 or `NoSuchUpload` with no final makes the provider attempt terminal. |
| `verification_method` | `varchar(32)` | For M2 completion, exactly `FULL_STREAM_SHA256`. |
| `observed_sha256` | `char(64)` | Application-computed digest from the final provider stream. |
| `observed_size_bytes` | `bigint` | Final object size observed by the verifier. |
| `verification_read_bytes` | `bigint` | Bytes actually consumed by the verifier. |
| `verification_started_at` | `timestamptz` | Optional diagnostic start time. |
| `verification_completed_at` | `timestamptz` | Required for multipart `COMPLETED`. |
| `verifier_implementation` | `varchar(128)` | Bounded implementation/version identifier for reproducibility. |
| `abort_requested_at` | `timestamptz` | Required in `ABORTING`/`ABORTED`. |

Do not duplicate `final_object_key`: `upload_sessions.blob_id -> blobs.object_key` is the only final
key source. Do not add `temporary_object_key`: selected Option A has no temporary object.

Replace checks while preserving M1 values:

```text
strategy IN ('SINGLE_PUT', 'MULTIPART')
state IN ('CREATED', 'IN_PROGRESS', 'COMPLETING',
          'COMPLETED', 'ABORTING', 'ABORTED', 'FAILED')
```

Strategy-specific checks require M2-only fields null for `SINGLE_PUT`. For `MULTIPART`, plan fields
are non-null and provider-ID requirements follow state. Existing M1 transitions remain legal.
Multipart transitions follow [the state-machine document](M2_STATE_MACHINES.md).

Rebuild `uq_upload_sessions_active_blob` for `CREATED`, `IN_PROGRESS`, `COMPLETING`, and
`ABORTING`. This enforces one resumable attempt per Blob, not the deployment-wide invocation cap.
An abandoned active session remains resolvable after its admission lease expires.

## New `upload_parts` table

```text
upload_parts
  upload_session_id        uuid        FK upload_sessions(id) ON DELETE RESTRICT
  part_number              integer
  offset_bytes             bigint
  size_bytes               bigint
  sha256                   char(64)
  state                    varchar(16)  PENDING|UPLOADED|VERIFIED
  provider_etag            text null
  provider_checksum_sha256 char(64) null
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
- `UPLOADED` requires `uploaded_at`;
- `VERIFIED` requires an opaque provider ETag, provider SHA-256 equal to expected SHA-256, and
  `verified_at`;
- capability timestamps/count are optional diagnostic metadata and never part state or progress;
- receipt/checksum lengths are bounded; ETag is never an integrity predicate by itself;
- session must use `MULTIPART` strategy; and
- no mutable completed/progress counters are stored.

Different part numbers may contain identical bytes and therefore may have identical expected
SHA-256 values, provider checksums, and ETags. None of those fields is unique. Uniqueness applies
only to `(upload_session_id, part_number)` and the immutable non-overlapping offset/range plan.

## New `multipart_admission_leases` table

```text
multipart_admission_leases
  upload_session_id uuid        PRIMARY KEY FK upload_sessions(id) ON DELETE CASCADE
  owner_id          uuid        NOT NULL
  epoch             bigint      NOT NULL CHECK (epoch > 0)
  acquired_at       timestamptz NOT NULL
  renewed_at        timestamptz NOT NULL
  expires_at        timestamptz NOT NULL
```

The row persists across expiry so takeover can atomically increment `epoch`. Under a fixed
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
Every mutating repository method requires the matching owner/epoch and rechecks that the lease is
unexpired immediately before its DB write. Status reads do not require a lease.

## Deferred constraint triggers

Use PostgreSQL constraint triggers so one transaction can insert a complete plan before sealing it.

1. **Plan completeness:** before `CREATED -> IN_PROGRESS`, rows are consecutive `1..N`, offsets are
   exact, non-final sizes equal frozen part size, final size is `1..part_size`, and summed bytes equal
   immutable Blob size.
2. **Plan immutability:** after `IN_PROGRESS`, part number/offset/size/SHA and session plan fields
   cannot update or delete. State and provider observations may change only through legal edges.
3. **Session identity:** Blob, initiating Version, strategy, provider upload ID after first non-null,
   and plan digest are immutable. A part cannot move to another session.
4. **Completing gate:** entering `COMPLETING` requires all `N` rows `VERIFIED`, a non-null opaque
   ETag and exact provider checksum on every part, and `completion_attempted_at`. ETags, expected
   digests, and provider checksums are not required to be distinct.
5. **Guarded recovery gate:** `COMPLETING -> IN_PROGRESS` requires unchanged provider upload ID,
   `final_absence_observed_at`, a fresh `last_provider_reconciled_at`, a non-409 completion result,
   a structurally valid subset of matching parts, and at least one unresolved part. Matching rows
   stay `VERIFIED`; absent/mismatching rows become `PENDING` in the same transaction.
6. **Attempt invalidation gate:** 409 or `NoSuchUpload` with no final requires
   `provider_attempt_invalidated_at` and a terminal `FAILED` attempt before another active session
   can be created. A new session starts with new part rows and no adopted old receipts.
7. **Completed gate:** multipart `COMPLETED` requires Blob `AVAILABLE`, every part `VERIFIED`, and
   immutable verification evidence satisfying:
   `verification_method = 'FULL_STREAM_SHA256'`, `observed_sha256 = blobs.sha256`,
   `observed_size_bytes = blobs.size_bytes`, and `verification_read_bytes = blobs.size_bytes`.
8. **Blob availability gate:** an associated locked multipart session must carry the same
   structurally consistent evidence. Existing M1 availability paths remain legal.
9. **Lease fence:** capability diagnostics, part/session transitions, completion, and abort writes
   require the current unexpired lease owner/epoch. Terminal transitions release or expire the lease.
10. **Terminal guards:** `COMPLETED`, `ABORTED`, and `FAILED` session rows cannot mutate. AVAILABLE
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
7. deploy only the M2-capable server binary;
8. run migration, M1 regression, DB/provider contract, and health smoke tests;
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
2. if reconciliation is necessary, start one isolated M2 control instance with new admission
   disabled, then stop it again before downgrade;
3. block rollback while any non-terminal multipart session exists;
4. reconcile/complete or explicitly abort known attempts and prove no known provider MPU remains;
5. export terminal multipart session, part, lease, and verification diagnostics to approved archive;
6. in one reviewed transaction, delete only terminal M2 workflow/lease/part rows required for schema
   downgrade; never delete DatasetVersion, DatasetEntry, Blob, or object data;
7. verify all `MULTIPART` session and `upload_parts`/lease counts are zero;
8. run the guarded Alembic downgrade and confirm the v0.1.0 schema contract; and
9. only then deploy v0.1.0 and re-enable M1 admission.

`READY` DatasetVersions and `AVAILABLE` Blobs created through M2 remain valid and pull-compatible
because their identity and publication semantics are unchanged. Rollback must never overwrite or
delete an AVAILABLE object. If terminal workflow history cannot be safely archived/removed, or any
downgrade precondition is uncertain, do not downgrade: restore the PostgreSQL backup or roll forward.

## Schema downgrade guard

Alembic downgrade fails closed if any `MULTIPART` session, `upload_parts` row, or admission-lease row
exists. It may drop M2 tables/columns and restore original checks/index/trigger only when all three
counts are zero. Silently discarding active or historical multipart state is forbidden.

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
- direct SQL cannot complete without structurally valid verification evidence;
- actual provider streamed verification is proven separately by integration tests;
- downgrade refuses non-terminal or unarchived M2 workflow rows; and
- rollback preserves READY Versions, AVAILABLE Blobs, and final objects byte-for-byte.
