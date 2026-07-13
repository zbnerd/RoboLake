# ADR 0009: Multipart Final Publication

- **Status:** Accepted
- **Date:** 2026-07-13

## Context

Multipart completion normally replaces an existing object at the same key. Concurrent uploads can
therefore violate immutable content addressing. Multipart SHA-256 is composite on the pinned MinIO
provider and is not the canonical SHA-256 of the complete file. A temporary-object design avoids
placing client bytes at the final key before verification, but requires safe large-object copy.

Provider probes showed:

- unconditional competing completion overwrote final bytes;
- `CompleteMultipartUpload(If-None-Match: *)` preserved an existing object and converged 200/412;
- destination `If-None-Match` on small `CopyObject` was ignored by pinned MinIO and overwrote bytes;
- multipart copy could use conditional final completion, but exposed no SHA-256 and doubled
  lifecycle/I/O; and
- repeated/lost completion outcomes were recoverable only through the deterministic final key.

## Decision

Upload multipart directly to the deterministic final Blob key. Only the API submits completion, with
`If-None-Match: *` and its provider-reconciled ordered part receipts. A 200, 412, 409, timeout, or
lost response is not sufficient to mark success.

HTTP status 200 alone is never completion proof: CompleteMultipartUpload can embed an error after
sending initial 200 headers. The SDK/provider adapter must parse and surface the final body outcome;
an embedded error leaves the session non-terminal and triggers deterministic reconciliation.

For every newly visible final multipart object whose Blob is not already `AVAILABLE`, stream the
entire object and compare its SHA-256/size with immutable Blob identity. Only an exact match permits
`Blob -> AVAILABLE` and session `COMPLETED`. A matching concurrent object is adopted. A mismatch
returns `STORED_OBJECT_MISMATCH` and requires operator intervention; it is never overwritten or
automatically deleted.

This supersedes only ADR 0004's prospective M2 claim that a provider system full-object SHA-256 is
normally available for multipart. The pinned provider exposes only a composite SHA-256, so M2
full-byte verification is mandatory rather than a missing-checksum fallback. ADR 0004's historical
and accepted M1 direct-transfer decision remains unchanged.

## Concrete failure prevented

Two clients complete different MPUs to one SHA key. Without a precondition, the later completion
silently replaces bytes already referenced by READY data. With conditional completion, one wins and
the other receives 412. Whole-byte verification then prevents a composite checksum or erroneous
client part plan from becoming an AVAILABLE attestation.

## Consequences

- Final publication costs one additional full provider read for every new multipart Blob.
- Metrics expose completed-object bytes, verification-read bytes, and whole-object verification
  duration separately from new/reused part payload bytes; none claim exact wire traffic.
- A buggy/malicious trusted caller can still cause wrong bytes to occupy a non-AVAILABLE final key
  before detection; ADR 0007's detect/report/stop boundary applies.
- There is no temporary namespace, temporary-object deletion, or second copy state.
- A losing incomplete MPU is explicitly aborted; unknown MPUs rely on stale-provider cleanup.
- M1 create-only keys, AVAILABLE meaning, READY meaning, and no-overwrite/no-delete rules remain
  unchanged.

## Alternatives considered

### Temporary key plus single CopyObject

Rejected because objects above 5 GB require multipart copy on AWS and the pinned MinIO ignored the
destination create-only condition for CopyObject.

### Temporary key plus multipart copy

Rejected for M2 because it adds a second resumable MPU, transient duplicate storage, copy I/O, two
verification reads on MinIO, and temporary deletion. Copy parts exposed no SHA-256, so it did not
remove final verification. Revisit only with a changed trust model/provider contract.

### Trust multipart ETag or composite SHA-256

Rejected because neither equals the canonical whole-file SHA-256.

### Proxy verified bytes through the API

Rejected because the API would become a second multi-gigabyte data path and M2 would no longer be a
direct-transfer extension.

## Related documents

- [Provider probes](../M2_PROVIDER_PROBES.md)
- [Failure matrix](../M2_FAILURE_AND_RECONCILIATION_MATRIX.md)
- [Poisoned final-key operator runbook](../M2_POISONED_FINAL_KEY_RUNBOOK.md)
- [ADR 0003](0003-object-storage-and-content-addressed-blobs.md)
- [ADR 0007](0007-failed-publication-and-manual-repair.md)
