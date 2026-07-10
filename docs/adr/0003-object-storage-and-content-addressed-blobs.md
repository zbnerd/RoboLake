# ADR 0003: Object Storage and Content-Addressed Blobs

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

Robot datasets contain large opaque files. PostgreSQL is appropriate for queryable ownership,
logical paths, and workflow state, but not for moving and serving multi-gigabyte file bodies.
Object keys based on dataset names or local paths would couple physical layout to mutable human
names, make deduplication difficult, and expose potentially confidential path information.

## Decision

Store raw bytes in one S3-compatible bucket and metadata in PostgreSQL. A blob is identified by the
pair `(sha256, size_bytes)`, with SHA-256 as its primary identity. The deterministic object key is:

```text
blobs/sha256/<first-2-hex>/<next-2-hex>/<64-char-lowercase-sha256>
```

Dataset entries map logical `relative_path` values to blobs. Dataset names, version numbers, and
logical paths never appear in object keys. A verified `AVAILABLE` blob can be reused by many entries
and versions; RoboLake never issues another write URL for it. A hash match with a different size is
an integrity conflict.

The bucket is unversioned for v0.1. Normal application operation does not delete completed objects.
Object metadata may repeat the expected hash and size, but it is diagnostic only. Before a blob
becomes `AVAILABLE`, the API streams the completed object and independently matches full-file
SHA-256 and byte count. Downloads repeat those checks.

## Consequences

- Identical bytes upload once and can be referenced by multiple logical paths or versions.
- Physical storage is independent of dataset naming and directory layout.
- A user cannot browse object keys to recover meaningful filenames; the registry is required.
- Full verification adds one storage read for each newly completed blob.
- PostgreSQL backup and object-store durability are both necessary; neither system alone represents
  a complete RoboLake registry.
- Garbage collection is deferred because v0.1 has no observed deletion/retention requirements.

## Alternatives considered

### Store blobs in PostgreSQL

Rejected because database connections, backups, and replication would carry large file bodies and
the API would become the transfer path.

### Use logical paths as object keys

Rejected because renames and versions could overwrite one another, deduplication would require
copies, and keys would leak user-visible names.

### Depend on object-store versioning for dataset versions

Rejected because provider object versions do not model a dataset-wide atomic manifest or searchable
logical metadata, and support varies among S3-compatible products.

## Related documents

- [Architecture: object key layout](../ARCHITECTURE_V0_1.md#7-object-key-layout)
- [ADR 0002: immutable versions](0002-immutable-dataset-versions.md)
