# ADR 0006: M1 Conditional Single-PUT Slice

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

M1 must prove one complete push/status/manifest/pull outcome without prematurely implementing M2
multipart lifecycle. A normal presigned PUT can be replayed until expiry and overwrite an existing
content-addressed key. Batched download URLs can also expire before a sequential CLI uses them.

## Decision

M1 uses one synchronous, sequential transfer per unique Blob:

- reject any file above 5,000,000,000 bytes before Dataset, Version, session, or object mutation;
- sign every PUT for exact key/method, Content-Length, `x-amz-checksum-sha256`, and
  `If-None-Match: *`;
- treat `200` as created, `412` as requiring existing-object reconciliation, and `409` as ambiguous:
  reconcile once, then return retryable conflict if no matching object is visible;
- complete without requiring ETag; verify HEAD system SHA-256/size and use one streamed full-GET
  fallback only when the system checksum is absent;
- adopt a matching pre-existing object, but never overwrite or automatically delete a mismatch;
- persist resume at whole-Blob granularity and reuse `AVAILABLE` attestations;
- return at most one READY-entry GET capability per download-plan response.

Download cursors are stateless canonical unpadded base64url over a fixed-width format byte,
DatasetVersion UUID, and manifest ordinal. They detect malformed input and cross-Version reuse but
are unsigned and are not authorization. Replaying a cursor returns the same entry with a fresh URL.
The official CLI advances only after safe materialization.

Pull builds a private sibling staging tree and publishes it with Linux
`renameat2(RENAME_NOREPLACE)` or macOS `renamex_np(RENAME_EXCL)`. Unsupported no-replace semantics
fail closed. M1 guarantees atomic visibility, not power-loss durability or pull resume.

## Consequences

- Stale write capabilities cannot mutate an existing key on a conforming provider.
- Concurrent pushes converge on one Blob/object and one dataset-scoped Version identity.
- A failed large single PUT restarts from zero; M2 provides within-file multipart resume.
- One-at-a-time GET issuance trades control-plane calls for bounded capability exposure and fresh TTL.
- Pinned MinIO integration tests are a provider compatibility gate, including conditional writes,
  checksum rejection, reconciliation, and URL-expiry behavior.

## Alternatives considered

### Proxy bytes through FastAPI

Rejected because the API becomes a bandwidth, timeout, and restart bottleneck.

### Give the CLI permanent object-storage credentials

Rejected because credential distribution and key scope exceed the observed workflow.

### Add multipart or batch download renewal in M1

Rejected because both expand lifecycle state before the single-PUT vertical slice is proven.

## Related documents

- [M1 design](../superpowers/specs/2026-07-11-m1-vertical-slice-design.md)
- [ADR 0003: content-addressed Blobs](0003-object-storage-and-content-addressed-blobs.md)
- [ADR 0004: M2 multipart target](0004-resumable-upload-strategy.md)
- [ADR 0007: failed publication and manual repair](0007-failed-publication-and-manual-repair.md)
