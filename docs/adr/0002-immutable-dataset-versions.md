# ADR 0002: Immutable Dataset Versions

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

A user needs to know that a version downloaded tomorrow means the same file tree registered today.
Mutable entry lists or mutable path-to-blob mappings would make provenance unverifiable, invalidate
manifest hashes, and make retries ambiguous.

Upload progress must still evolve, and a failed transfer must be repairable without changing the
declared content.

## Decision

Registering a version is one atomic operation that validates all entries, stores them, computes or
confirms canonical manifest SHA-256, and seals the version. From that point:

- `manifest_schema_version`, `manifest_sha256`, entry ordinals, paths, sizes, and hashes never
  change;
- DatasetVersion UUID is resource identity; manifest SHA-256 is a content fingerprint, not a primary
  key; `(dataset_id, manifest_sha256)` is dataset-scoped snapshot identity;
- `(dataset_id, version_number)` is unique and numbers increase by immutable-manifest registration
  order, not READY order or upload completion time;
- `(dataset_id, manifest_sha256)` is unique, so registering identical content returns the existing
  version rather than inventing another number;
- lifecycle state and operational failure fields may change only through explicit transitions;
- `READY` is terminal and cannot be updated or deleted in v0.1;
- changed content registers the next number even if an older Version is non-READY; restoring the old
  source manifest can still resume the older Version;
- `FAILED` may return to `UPLOADING` only for the same expected Blob hashes after its retry or
  operator-resolved precondition is satisfied. Recovery never edits the manifest.

The database enforces sealing and legal transitions with constraints and triggers in addition to
domain checks. Upload sessions are attempts attached to expected blobs; they are replaceable without
replacing the version.

## Consequences

- UUID, dataset-scoped manifest identity, and human `<dataset>@vN` reference are distinct durable
  provenance layers.
- Different Datasets may own separate Version UUIDs/lineage for the same manifest while sharing
  physical Blobs.
- Idempotent retry can distinguish “same operation” from conflicting content.
- Status changes do not alter content identity.
- Correcting a genuinely wrong manifest requires a new version; there is no in-place edit.
- Version deletion and blob garbage collection are deferred because their retention semantics are
  not yet observed.

## Alternatives considered

### Mutable draft entries until `READY`

Rejected. An upload could then resume against a different path or hash, and partial work would not
have a stable content contract.

### Treat only the latest dataset state as authoritative

Rejected. It loses reproducibility and cannot reconstruct what was transferred previously.

### Rely only on application-layer checks

Rejected. Concurrent code paths, migrations, or operator SQL could violate invariants. PostgreSQL
must provide the final enforcement boundary.

## Related documents

- [Architecture: domain model and schema](../ARCHITECTURE_V0_1.md#5-domain-model)
- [ADR 0003: object storage and blobs](0003-object-storage-and-content-addressed-blobs.md)
- [ADR 0007: failed publication and manual repair](0007-failed-publication-and-manual-repair.md)
