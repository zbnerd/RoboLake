# ADR 0010: Multipart Reconciliation

- **Status:** Accepted
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
3. paginated ListParts for an existing provider upload;
4. frozen database plan for intended parts; and
5. stable error/action based on the resulting facts.

DB progress never overrides provider absence. A matching provider part becomes VERIFIED; absent or
mismatching incomplete parts return to PENDING and may be replaced with the exact frozen bytes. A
412 triggers final reconciliation. A 409 with no matching final ends that provider attempt because
AWS requires a new MPU. The old upload ID and its parts are never resumed: after abort/invalidation,
`RETRY_PUSH` creates a new MPU and uploads every part again. `NoSuchUpload` plus matching final
completes; with no final it ends the attempt and creates a new one.

Abort uses `ABORTING` until provider absence is proven. Explicit abort applies only to the same
incomplete workflow; Ctrl-C preserves resumability. Provider seven-day stale cleanup bounds unknown
Create-response-loss MPUs. No M2 background sweeper, final deletion, repair, or Blob GC is added.

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
- ListParts pagination and final full reads add provider requests and latency.
- Retryable attempt failure does not invent a new DatasetVersion or mutate its manifest.
- Operator intervention remains required for a mismatching final key.
- M1 error envelope, derived actions, exit categories, and publication meanings remain intact.

## Alternatives considered

### Trust database receipts

Rejected because crashes can occur between provider and DB commits.

### Trust client ETags/status

Rejected because the client can lose responses and ETag is not integrity proof.

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
