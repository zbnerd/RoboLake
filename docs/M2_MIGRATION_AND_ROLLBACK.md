# M2 Migration and Rollback

## Migration objective

M2 adds resumable multipart attempts without changing v0.1.0 manifests, DatasetVersion rows, Blob
identity, READY/AVAILABLE triggers, or pull data. Existing M1 `SINGLE_PUT` sessions remain valid and
need no rewrite.

The proposed Alembic revision follows `20260711_0002` and is additive before constraints are
replaced. Exact revision identifiers are selected during implementation, not this design task.

## `upload_sessions` extension

Add nullable multipart fields:

| Column | Type | Rule |
| --- | --- | --- |
| `provider_upload_id` | `text` | Required in multipart `IN_PROGRESS`/`COMPLETING`/`ABORTING`; opaque and never exposed as an independent stable RoboLake API field. It may appear only inside a fully redacted capability URL. Partial unique when non-null. |
| `part_size_bytes` | `bigint` | Required for multipart; 64 MiB..5 GiB; frozen after provider initiation. |
| `planned_part_count` | `integer` | Required for multipart; 1..10,000; equals formula over Blob size. |
| `part_plan_sha256` | `char(64)` | SHA-256 of canonical ordered `(number, offset, size, digest)` plan; idempotency/freeze evidence. |
| `completion_attempted_at` | `timestamptz` | Required before/while `COMPLETING`; records intent, not success. |
| `verification_started_at` | `timestamptz` | Set before the final streamed GET. |
| `verification_completed_at` | `timestamptz` | Required for multipart `COMPLETED`. |
| `abort_requested_at` | `timestamptz` | Required in `ABORTING`/`ABORTED`. |

Do not duplicate `final_object_key`: `upload_sessions.blob_id -> blobs.object_key` is the only final
key source. Do not add `temporary_object_key`: selected Option A has no temporary object. This avoids
two SQL values that could disagree with immutable Blob identity.

Replace checks while preserving M1 values:

```text
strategy IN ('SINGLE_PUT', 'MULTIPART')
state IN ('CREATED', 'IN_PROGRESS', 'COMPLETING',
          'COMPLETED', 'ABORTING', 'ABORTED', 'FAILED')
```

Strategy-specific checks require all multipart fields null for `SINGLE_PUT`. For `MULTIPART`, the
part plan fields are non-null; provider ID requirements follow state. Existing M1 state transitions
remain legal. Multipart transitions follow [the state-machine document](M2_STATE_MACHINES.md).

Rebuild `uq_upload_sessions_active_blob` for states `CREATED`, `IN_PROGRESS`, `COMPLETING`, and
`ABORTING`. Concurrent creation handles a uniqueness violation by selecting the existing active row.

## New `upload_parts` table

```text
upload_parts
  upload_session_id       uuid        FK upload_sessions(id) ON DELETE RESTRICT
  part_number             integer
  offset_bytes            bigint
  size_bytes              bigint
  sha256                  char(64)
  state                   varchar(16)  PENDING|ISSUED|UPLOADED|VERIFIED
  provider_etag           text null
  provider_checksum_sha256 char(64) null
  capability_issued_at    timestamptz null
  capability_expires_at   timestamptz null
  uploaded_at             timestamptz null
  verified_at             timestamptz null
  last_error_code         varchar(64) null
  created_at              timestamptz

PRIMARY KEY (upload_session_id, part_number)
UNIQUE (upload_session_id, offset_bytes)
```

Checks:

- part number `1..10_000`, offset `>= 0`, size `> 0`, canonical lowercase SHA-256;
- `ISSUED` requires issue/expiry timestamps; `UPLOADED` requires `uploaded_at`;
- `VERIFIED` requires provider ETag, provider SHA-256 equal to expected SHA-256, and `verified_at`;
- receipt/checksum lengths are bounded; ETag is metadata, never an integrity predicate by itself;
- session must use `MULTIPART` strategy; and
- no mutable completed/progress counters are stored.

## Deferred constraint triggers

Use PostgreSQL constraint triggers so one transaction can insert a complete plan before sealing it.

1. **Plan completeness:** before `CREATED -> IN_PROGRESS`, rows are consecutive `1..N`, offsets are
   exact, non-final sizes equal frozen part size, final size is `1..part_size`, and summed bytes equal
   immutable Blob size.
2. **Plan immutability:** after `IN_PROGRESS`, part number/offset/size/SHA and session plan fields
   cannot update or delete. State/receipts may change only through legal transitions.
3. **Session identity:** Blob, initiating Version, strategy, provider upload ID (after first non-null),
   and plan digest are immutable. A part cannot move to another session.
4. **Completing gate:** `COMPLETING` requires all `N` rows `VERIFIED`, distinct provider ETags, exact
   provider checksums, and `completion_attempted_at`.
5. **Completed gate:** multipart `COMPLETED` requires referenced Blob `AVAILABLE`, exact final size,
   verification timestamps, and every part `VERIFIED`.
6. **Blob availability gate:** extend the Blob guard only for a transition caused by multipart: an
   associated locked session must have full verification evidence. It transitions to `COMPLETED`
   when its provider MPU disappeared after completion, or `ABORTING` when a still-present MPU lost
   to a matching final object. Existing M1 availability path remains legal.
7. **Terminal guards:** `COMPLETED`, `ABORTED`, and `FAILED` session rows cannot mutate. AVAILABLE
   Blob and READY Version guards remain unchanged.

Direct SQL therefore cannot mutate an AVAILABLE Blob, change boundaries, reparent a part, complete
without proof, or direct an upload to a key other than `blobs.object_key`. PostgreSQL cannot verify
provider bytes; it can require that the application records the exact verification facts in one
legal transaction.

## Upgrade order

1. Assert current Alembic head and take a PostgreSQL backup.
2. Add nullable session columns.
3. Create `upload_parts` and indexes.
4. Install new functions/triggers under versioned names.
5. Drop/recreate session strategy/state checks and active partial index in one transaction.
6. Validate every existing row is `SINGLE_PUT` with multipart fields null.
7. Enable application code only after migration success.
8. Run M1 regression and M2 DB/provider contract suites.

The migration acquires locks on `upload_sessions`; operators must schedule it when no active M1
pushes run. Dataset/Blob/Version tables receive only a trigger replacement, not data rewriting.

## Application rollback

If M2 application deployment fails before any multipart session is created, deploy v0.1.0 code
against the additive schema after confirming:

```sql
SELECT count(*) FROM upload_sessions WHERE strategy = 'MULTIPART';
-- must be 0
```

The old ORM ignores additive columns, but old code does not understand new state/strategy values.
Therefore application rollback is unsafe while any multipart row exists.

If multipart attempts exist:

1. stop new pushes but keep control-plane access;
2. let in-progress sessions complete or explicitly abort them;
3. reconcile every `COMPLETING`/`ABORTING` row;
4. verify no active multipart provider upload remains;
5. archive terminal session/part diagnostics if policy permits;
6. only then decide whether to delete terminal workflow rows and deploy v0.1.0.

READY Versions containing large Blobs remain pull-compatible because pull and Blob identity are
unchanged. v0.1.0 simply cannot create/resume their multipart uploads.

## Schema downgrade

Alembic downgrade must fail closed if any `MULTIPART` session or `upload_parts` row exists. It may
drop the table/columns and restore original checks/index/trigger only when both counts are zero.
Silently discarding active or historical multipart state is forbidden.

Database downgrade never removes object-store parts. Before a permitted downgrade, explicitly abort
known incomplete provider uploads and confirm ListParts returns `NoSuchUpload`; unknown create-
response-loss orphans remain bounded by provider stale cleanup.

## Roll-forward recovery

Preferred recovery is roll-forward: fix the application while retaining the additive schema and
provider parts. This preserves expensive resumable progress and avoids guessing whether a lost
Complete response published a final object.
