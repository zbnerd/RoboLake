# ADR 0008: Multipart Session Model

- **Status:** Proposed
- **Date:** 2026-07-13

## Context

A process can stop after any large-file part. PostgreSQL may disagree with provider ListParts, a
same-number UploadPart replaces prior bytes, and changing part boundaries on resume would make old
provider progress unsafe. Persisting only an upload ID and ETags would not bind stored parts to the
sealed Blob or survive client/database response loss safely.

## Decision

Extend the existing `UploadSession` with `MULTIPART` strategy and explicit `CREATED`, `IN_PROGRESS`,
`COMPLETING`, `COMPLETED`, `ABORTING`, `ABORTED`, and `FAILED` attempt states. Add `UploadPart` rows
with `PENDING`, `UPLOADED`, and `VERIFIED` states.

Capability issuance is optional diagnostic metadata, not part state, audit history, or transfer
progress. Reissuance is permitted. Only provider-reconciled `VERIFIED` parts contribute to progress:
PostgreSQL owns the immutable intended plan, while ListParts owns current provider presence.

Part size starts at 64 MiB, doubles until at most 10,000 parts are needed, and caps at 5 GiB. The
session freezes part size/count and exact number/offset/size/SHA-256 plan before provider initiation.
Provider IDs and ETags are opaque operational receipts. Different part numbers may carry identical
bytes and therefore identical expected hashes, provider checksums, and ETags; uniqueness applies
only to part number and the frozen non-overlapping ranges. PostgreSQL owns intent; paginated
ListParts owns current provider presence. One persistent active session per Blob and database
triggers enforce identity, plan, transition, and completion structure.

Separate the persistent resumable session from a short-lived admission lease. The lease stores an
invocation owner UUID, monotonically increasing epoch, and expiry. Only unexpired leases count
toward the global execution cap. Every mutating operation is fenced by the current owner/epoch;
lease expiry never expires or aborts the session/provider MPU. An expired lease may be atomically
reacquired with a higher epoch.

`COMPLETING -> IN_PROGRESS` is legal only when the final key is absent, the same provider MPU still
exists, completion was not 409, and a complete structurally valid ListParts result contains a proper
subset of matching parts. Matches stay VERIFIED and unresolved parts return to PENDING. A 409 or
`NoSuchUpload` with no final terminates the provider attempt; a new session/MPU starts with every
part unresolved and adopts no old receipt.

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
- Up to 10,000 rows and provider receipts are stored per active large Blob.
- Every resumed invocation rereads the local file to prove identity before adding parts.
- Abandoned invocations release capacity when their lease expires without discarding resumable
  session/provider progress.
- Lease fencing adds heartbeat and takeover transactions; an already dispatched provider request
  may settle and must be reconciled by the current owner.
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
