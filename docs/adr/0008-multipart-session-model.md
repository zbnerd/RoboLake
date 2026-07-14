# ADR 0008: Multipart Session Model

- **Status:** Proposed
- **Date:** 2026-07-13

## Context

A process can stop after any large-file part. PostgreSQL may disagree with provider ListParts, a
same-number UploadPart replaces prior bytes, and changing part boundaries on resume would make old
provider progress unsafe. Persisting only an upload ID and ETags would not bind stored parts to the
sealed Blob or survive client/database response loss safely.

## Decision

Extend the existing `UploadSession` with `MULTIPART` strategy and explicit `CREATED`, `INITIATING`,
`IN_PROGRESS`, `COMPLETING`, `COMPLETED`, `ABORTING`, `ABORTED`, `CANCELLED`, and `FAILED` attempt
states. Add `UploadPart` rows with `PENDING`, `UPLOADED`, and `VERIFIED` states.

Separate request replay, invocation ownership, and attempt identity:

- `request_id` UUID idempotently maps one create/resolve request to one session and is never rebound;
- `invocation_id` UUID identifies one CLI execution and its metrics/admission acquisition; and
- immutable positive `session_generation` identifies one attempt within a Blob, with
  `UNIQUE(blob_id, session_generation)` and at most one active generation.

A new request first replays its immutable request record, otherwise locks the Blob and resolves the
active session or creates `max(generation) + 1`. A new CLI invocation therefore resumes an active
session, while a new request after a terminal 409, `NoSuchUpload`, cancellation, abort, or ambiguous
initiation can create the next generation. Concurrent creators converge through the active-session
partial unique index; terminal sessions are never reactivated.

Capability issuance is optional diagnostic metadata, not part state, audit history, or transfer
progress. Reissuance is permitted. Only `VERIFIED` parts contribute to progress. PostgreSQL stores a
complete successful-response receipt: part number, opaque UploadPart response ETag, and Base64
`ChecksumSHA256`; paginated ListParts separately proves current provider presence. The expected part
digest remains lowercase hex, and the provider checksum must decode to the same 32 raw bytes. Client
input alone and ListParts-only observations are insufficient. Supported providers must return both
response fields. When the response receipt was lost, RoboLake re-uploads the same
checksum/length-bound part number to obtain a new receipt, then reconciles it. Complete sends
PartNumber plus both response fields for every part. AWS's general API makes the checksum field
optional; requiring it is RoboLake's checksum-enabled SHA-256 profile.

Part size starts at 64 MiB, doubles until at most 10,000 parts are needed, and caps at 5 GiB. Blob
size is capped at the portable AWS/MinIO intersection of 5,000,000,000,000 bytes. The
session freezes part size/count and exact number/offset/size/SHA-256 plan in the committed `CREATED`
registration, before provider initiation.
Its `part_plan_sha256` is SHA-256 over schema-v1 canonical JSON with top-level order
`schema_version`, `blob_size_bytes`, `part_size_bytes`, `part_count`, `parts`; each part orders
`part_number`, `offset_bytes`, `size_bytes`, `sha256`. UTF-8, compact separators, no trailing newline,
JSON integers only, ascending part number, and lowercase 64-character digests are normative.
Provider IDs and ETags are opaque operational receipts. Different part numbers may carry identical
bytes and therefore identical expected hashes, provider checksums, and ETags; uniqueness applies
only to part number and the frozen non-overlapping ranges. PostgreSQL owns intent; paginated
ListParts owns current provider presence. One persistent active session per Blob and database
triggers enforce identity, plan, transition, and completion structure.

Separate the persistent resumable session from a short-lived upload admission lease. Acquisition is
idempotent by request UUID and invocation UUID; the server generates the lease owner UUID and epoch.
Only unexpired leases count toward the upload cap. Repository mutations use atomic SQL predicates
over session, owner, epoch, and database-time expiry; a zero-row write is
`ADMISSION_LEASE_LOST`. Structural triggers do not claim to authenticate caller fence values.

Completion is separately server-owned. The complete request first looks up immutable idempotency by
scope/request ID and validates the request hash. A matching record returns its exact stored response
without a current lease; mismatch returns `IDEMPOTENCY_CONFLICT`. Only first execution validates the
upload fence and freezes reason `PARTS_READY` (all receipt-backed) or `FINAL_PRESENT` (adoption path).
One transaction sets `COMPLETING`/`PENDING`, creates pending work, releases the upload lease, and
stores the canonical 202 response. A unique race loser replays the winner. A bounded runner using the same
application artifact claims PostgreSQL `COMPLETING` rows with a distinct completion owner/epoch and
lease. Default completion concurrency is 2 (hard maximum 8); default lease/heartbeat are 120/30
seconds. It heartbeats in short transactions at most one third of TTL throughout provider Complete
and the full stream. A stale runner cannot commit evidence, Blob AVAILABLE, or terminal state; a
takeover reconciles final state and restarts interrupted full GET from byte zero.

Blob stays `UPLOADING` throughout provider completion, reconciliation, and whole-object GET/hash.
Session `completion_phase` is `PENDING`, `ASSEMBLING`, `FINAL_PRESENT`, or `FINAL_VERIFICATION`.
Only after a complete read does one short fenced transaction record evidence and move Blob
`UPLOADING -> VERIFYING -> AVAILABLE` plus session `COMPLETED`. Proven mismatch uses
`UPLOADING -> VERIFYING -> FAILED`; transient failure commits no Blob transition.

Provider initiation has an explicit crash boundary. `CREATED` means no provider request was sent and
may become `CANCELLED`. The API records `INITIATING` before CreateMultipartUpload. Only a durably
stored upload ID permits `IN_PROGRESS`; response loss or process failure before that commit becomes
terminal `FAILED` with initiation-ambiguous reason and releases admission in the same current-owner
fenced transaction. If the process still knows the in-memory ID after a DB failure it may
best-effort abort without claiming success. An abort while `INITIATING` returns a
retryable in-progress error, and an unaddressable orphan relies on provider lifecycle cleanup rather
than a false abort claim. Process death before the terminal transaction falls back to lease TTL.

`COMPLETING -> IN_PROGRESS` is legal only when the final key is absent, the same provider MPU still
exists, completion was not 409, and a complete structurally valid ListParts result supports safe
requeue. Ordinary completion ambiguity requires a proper subset of receipt-backed matching parts;
a vanished `FINAL_PRESENT` observation may requeue a fully receipt-backed set for new
`PARTS_READY` acceptance. Matches stay VERIFIED and unresolved or receipt-less parts return to
PENDING. A 409 or
`NoSuchUpload` with no final terminates the provider attempt; a new request allocates the next
generation/MPU with every part unresolved and no adopted old receipt.

The official CLI rehashes the complete file and deterministic parts before resumed transfer,
streams exact positional ranges, and uses a rolling capability window equal to configured
concurrency (default 4, maximum 16). Concurrency is operational and never changes stored boundaries.
The provider upload ID is not a stable independent RoboLake API field, although it necessarily
appears with the part number inside an opaque presigned capability URL that is fully redacted and
never interpreted by the client.

This supersedes only ADR 0004's prospective M2 routing/session details: M2 activates above the
released 5,000,000,000-byte single-PUT limit, not at 64 MiB, and provider disappearance is reconciled
rather than interpreted through a fixed application-session expiry. ADR 0004's historical and
accepted M1 decisions, including API-controlled presigned direct transfer, remain unchanged.

The first M2 release uses an offline rollout. Every v0.1.0 server and non-terminal M1 transfer is
drained/stopped before migration and M2 admission; mixed v0.1.0/M2 serving is unsupported. Rollback
is blocked until non-terminal M2 attempts are resolved and terminal workflow diagnostics are
archived/removed without touching READY Versions, AVAILABLE Blobs, or final objects.

## Concrete failure prevented

If DB says part 7 uploaded but a crash occurred before the provider accepted it, trusting DB would
complete with a hole. If the client chose a new part size after restart, provider part 7 would refer
to different bytes. Frozen boundaries plus provider reconciliation reset the first case to PENDING
and reject the second before another byte is sent.

## Consequences

- Resume reuses only provider-proven, exact parts and survives CLI/API restarts.
- A missing UploadPart response receipt causes only that exact part to be retransmitted for portable
  completion; ListParts observations alone never become the completion manifest.
- Up to 10,000 rows and provider receipts are stored per active large Blob.
- Every resumed invocation rereads the local file to prove identity before adding parts.
- Abandoned invocations release capacity when their lease expires without discarding resumable
  session/provider progress.
- Upload and completion leases add separate heartbeat/takeover transactions; an already dispatched
  provider request may settle and must be reconciled by the current owner.
- PostgreSQL supplies the completion work registry and claim source without Kafka, Celery, or another
  external queue. A failed verification restarts from byte zero in M2.
- `ABORTING` is required because abort and DB acknowledgements can be lost independently.
- M1 manifests, Blob/Version identity, single-PUT rows, metrics, and pull semantics do not change.

## Alternatives considered

### Store only upload ID and ETags

Rejected because ETags do not prove expected bytes/boundaries and DB progress can drift from
provider state.

### Let each client choose part size

Rejected because the same session could be interpreted differently after restart or on another
machine.

### Issue every missing capability

Rejected because thousands of bearer URLs can expire unused and enlarge exposure. One-at-a-time
was also rejected because it serializes high-latency links; a bounded rolling window is the smaller
credible throughput compromise.

### Terminal `FAILED` part state

Rejected. Missing/mismatching incomplete parts can be safely replaced; truly fatal conditions
terminate the parent attempt.

### Count persistent sessions against the global execution cap

Rejected because an abandoned but intentionally resumable session could retain a slot forever even
after provider lifecycle cleanup. Expiring admission leases reclaim execution capacity without
inventing session expiry, background GC, or automatic MPU deletion.

## Related documents

- [Multipart architecture](../M2_MULTIPART_ARCHITECTURE.md)
- [State machines](../M2_STATE_MACHINES.md)
- [Migration and rollback](../M2_MIGRATION_AND_ROLLBACK.md)
