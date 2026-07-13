# ADR 0008: Multipart Session Model

- **Status:** Accepted
- **Date:** 2026-07-13

## Context

A process can stop after any large-file part. PostgreSQL may disagree with provider ListParts, a
same-number UploadPart replaces prior bytes, and changing part boundaries on resume would make old
provider progress unsafe. Persisting only an upload ID and ETags would not bind stored parts to the
sealed Blob or survive client/database response loss safely.

## Decision

Extend the existing `UploadSession` with `MULTIPART` strategy and explicit `CREATED`, `IN_PROGRESS`,
`COMPLETING`, `COMPLETED`, `ABORTING`, `ABORTED`, and `FAILED` attempt states. Add `UploadPart` rows
with `PENDING`, `ISSUED`, `UPLOADED`, and `VERIFIED` states.

`ISSUED` records only that an exact capability was created; it is audit state, not transfer
progress. Reissuance is permitted. Only provider-reconciled `VERIFIED` parts contribute to progress:
PostgreSQL owns the immutable intended plan, while ListParts owns current provider presence.

Part size starts at 64 MiB, doubles until at most 10,000 parts are needed, and caps at 5 GiB. The
session freezes part size/count and exact number/offset/size/SHA-256 plan before provider initiation.
Provider IDs and ETags are opaque operational receipts. PostgreSQL owns intent; paginated ListParts
owns current provider presence. One active session per Blob and database triggers enforce identity,
plan, transition, and completion constraints.

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

## Concrete failure prevented

If DB says part 7 uploaded but a crash occurred before the provider accepted it, trusting DB would
complete with a hole. If the client chose a new part size after restart, provider part 7 would refer
to different bytes. Frozen boundaries plus provider reconciliation reset the first case to PENDING
and reject the second before another byte is sent.

## Consequences

- Resume reuses only provider-proven, exact parts and survives CLI/API restarts.
- Up to 10,000 rows and provider receipts are stored per active large Blob.
- Every resumed invocation rereads the local file to prove identity before adding parts.
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

## Related documents

- [Multipart architecture](../M2_MULTIPART_ARCHITECTURE.md)
- [State machines](../M2_STATE_MACHINES.md)
- [Migration and rollback](../M2_MIGRATION_AND_ROLLBACK.md)
