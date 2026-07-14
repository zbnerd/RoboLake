# ADR 0010: Multipart Reconciliation

- **Status:** Proposed
- **Date:** 2026-07-13

## Context

Network and process failures can lose acknowledgements after UploadPart, Complete, or Abort. A
repeated Complete returned 412 with a condition and `NoSuchUpload` without it on pinned MinIO.
Provider parts can disappear independently of stale DB receipts. Treating any single response as
truth can resend valid data, skip missing data, leak parts, or incorrectly fail an immutable Version.

## Decision

Use deterministic reconciliation rather than response interpretation:

1. final key and existing AVAILABLE attestation first;
2. full-byte final verification for any non-AVAILABLE object;
3. UploadPart response receipts retained in PostgreSQL;
4. paginated ListParts for current provider presence/checksum verification;
5. the frozen database plan for intended parts; and
6. stable error/action based on the resulting facts.

DB progress never overrides provider absence. A provider part becomes VERIFIED only when ListParts
matches its frozen size/checksum and a complete successful UploadPart response receipt containing
part number, response ETag, and Base64 `ChecksumSHA256`. A matching ListParts entry without both
durable response fields remains unresolved and is re-uploaded to obtain them. Absent or
mismatching incomplete parts return to PENDING and may be replaced with the exact frozen bytes. A
412 triggers final reconciliation. After an ambiguous non-409 completion, final absence plus a
structurally valid partial ListParts result for the same existing MPU permits guarded
`COMPLETING -> IN_PROGRESS`: matching parts stay VERIFIED and only unresolved parts are retried. If
all parts remain after an ordinary `PARTS_READY` completion ambiguity, the session stays COMPLETING
and retries Complete. If a `FINAL_PRESENT` observation disappears, the session safely requeues even
a fully matching set so only a new `PARTS_READY` acceptance may call Complete.

A 409 with no matching final ends that provider attempt because AWS requires a new MPU. The old
upload ID and its parts are never resumed: after abort/invalidation, `RETRY_PUSH` creates a new
session/MPU and uploads every part again. `NoSuchUpload` plus matching final completes; with no final
it uses the same full-restart rule and maps to `MULTIPART_SESSION_NOT_FOUND`. The next resolve uses a
new request UUID and creates or converges on the next immutable session generation; an earlier
request record is never rebound. RoboLake does not infer provider expiry from `NoSuchUpload`.

Persistent sessions are fenced during upload by separate expiring admission leases. Only unexpired
leases count toward the upload cap. Takeover increments the lease epoch without aborting provider
MPU; stale owners cannot mutate DB state or initiate control calls. A previously issued exact part
URL or already dispatched API provider request may still settle, and the current owner reconciles
that provider fact.

Complete is accepted asynchronously. The upload lease ends when `COMPLETING` is committed, and a
PostgreSQL-claimed server runner owns Complete plus full-object verification under a distinct
renewable completion lease and independent short heartbeats throughout long I/O. Client disconnect
does not cancel that work. A dead or fenced runner cannot commit; a later owner reconciles final state
and repeats verification from byte zero if necessary.

Completion request replay precedes upload-lease fencing. An existing matching operation/request hash
returns its exact stored 202 without lease/session action; mismatch is `IDEMPOTENCY_CONFLICT`. Only
first execution validates the lease/parts and atomically sets `COMPLETING`/`PENDING`, creates pending
work, releases admission, and stores that 202. A concurrent unique-key loser rereads the winner.

Blob remains `UPLOADING` throughout provider Complete, final reconciliation, and full streamed
verification. Session phase (`PENDING`, `ASSEMBLING`, `FINAL_PRESENT`, `FINAL_VERIFICATION`) carries
long-work progress. A successful complete read is published through one short fenced transaction:
evidence plus Blob `UPLOADING -> VERIFYING -> AVAILABLE` and session `COMPLETED`. Proven mismatch
uses `UPLOADING -> VERIFYING -> FAILED`; transient failure or guarded same-MPU recovery requires no
reverse Blob transition.

`CREATED` means provider initiation has not started and may become `CANCELLED`. `INITIATING` records
that CreateMultipartUpload may have been sent but no upload ID is durable. It becomes `IN_PROGRESS`
only with a stored ID. Response/commit loss becomes terminal initiation ambiguity; a new request may
allocate the next generation while unaddressable residue is left to lifecycle cleanup. Recording
that ambiguity and releasing admission happen atomically under the current owner/epoch; process
death before that transaction falls back to TTL. Abort during
INITIATING is retryably rejected; `ABORTING` is legal only with a durable provider ID.

Abort uses `ABORTING` until provider absence is proven. Explicit abort applies only to the same
incomplete workflow; Ctrl-C preserves resumability. Provider seven-day stale cleanup bounds unknown
Create-response-loss MPUs and a known losing MPU whose best-effort completion-owner abort fails. No
M2 background sweeper, final deletion, repair, or Blob GC is added.

This supersedes only ADR 0004's prospective M2 expiry and reconciliation assumptions. It does not
rewrite or invalidate ADR 0004's historical M1 decisions, the released single-PUT workflow, or any
frozen M1 identity and publication semantics.

## Concrete failure prevented

The provider completes an object but its response is lost. Retrying Complete can return 412 or
`NoSuchUpload`; treating either as failure would upload the whole file again. HEAD/full GET of the
deterministic key proves the existing result and converges to one AVAILABLE Blob without another
write.

## Consequences

- Every control operation is replayable across API instances without server-memory ownership.
- Request replay never changes session identity; new requests after terminal attempts allocate the
  next Blob-scoped generation.
- ListParts pagination and final full reads add provider requests and latency.
- Missing UploadPart response ETag or `ChecksumSHA256` causes safe exact-part retransmission even when
  ListParts shows matching bytes; this is the cost of RoboLake's AWS-documented, checksum-enabled
  completion-receipt profile.
- Upload and completion lease heartbeat/takeover add operational DB writes but prevent abandoned
  clients or workers from permanently exhausting their separate caps.
- Retryable attempt failure does not invent a new DatasetVersion or mutate its manifest.
- Operator intervention remains required for a mismatching final key.
- M1 error envelope, derived actions, exit categories, and publication meanings remain intact.

## Alternatives considered

### Trust database receipts

Rejected because crashes can occur between provider and DB commits.

### Trust an unverified client receipt or ListParts-only observation

Rejected because the client can lie/lose responses, AWS directs clients to retain UploadPart response
ETags rather than build Complete input from ListParts, and ETag is not integrity proof. RoboLake's
profile requires response ETag plus `ChecksumSHA256` and matching current ListParts evidence before
VERIFIED.

### Treat `NoSuchUpload` as definitive failure

Rejected because completion may already have made the final object visible.

### Automatically delete mismatch and retry

Rejected because final-key blast radius cannot be proven; it violates ADR 0007.

### Add an application cleanup daemon

Rejected because M2 needs only current-workflow abort plus provider lifecycle. General operational
cleanup remains an evidence-driven later milestone.

## Related documents

- [Failure and reconciliation matrix](../M2_FAILURE_AND_RECONCILIATION_MATRIX.md)
- [Security model](../M2_SECURITY_MODEL.md)
- [ADR 0008](0008-multipart-session-model.md)
- [ADR 0009](0009-multipart-final-publication.md)
