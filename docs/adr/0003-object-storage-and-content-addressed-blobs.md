# ADR 0003: Object Storage and Content-Addressed Blobs

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

Robot datasets contain large opaque files. PostgreSQL is appropriate for queryable ownership,
logical paths, and workflow state, but not for moving and serving multi-gigabyte file bodies.
Object keys based on dataset names or local paths would couple physical layout to mutable human
names, make deduplication difficult, and expose potentially confidential path information.

## Decision

Store raw bytes in one S3-compatible bucket and metadata in PostgreSQL. Blob rows use UUID relational
identity; SHA-256 is globally unique physical content identity and size disagreement for one digest
is an integrity conflict. The deterministic object key is:

```text
blobs/sha256/<first-2-hex>/<next-2-hex>/<64-char-lowercase-sha256>
```

Dataset entries map logical `relative_path` values to blobs. Dataset names, version numbers, and
logical paths never appear in object keys. A verified `AVAILABLE` blob can be reused by many entries
and versions; it is a persistent verification attestation, not merely “upload completed.” RoboLake
never rereads it for each new Version and never issues another write URL for it.

The bucket is unversioned for v0.1. Every M1 PUT is create-only and signs exact key/method,
`If-None-Match: *`, Content-Length, and expected SHA-256. Existing content is reconciled, never
overwritten. Normal application operation does not delete objects. Before `AVAILABLE`, API HEAD
requires exact size and provider system `ChecksumSHA256`; absence of that system checksum triggers
a streamed full-GET fallback. Caller metadata and ETag are not integrity proof. Downloads always
rehash actual bytes.

Canonical DatasetEntry contains only relative path, size, and SHA-256. Suffix-derived media or
format hints are non-authoritative enrichment and are not accepted, persisted, or hashed in M1.

## Consequences

- Identical bytes upload once and can be referenced by multiple logical paths or versions.
- Physical storage is independent of dataset naming and directory layout.
- A user cannot browse object keys to recover meaningful filenames; the registry is required.
- Matching system checksum avoids a second full read; a provider without it pays one fallback read.
- Replayed URLs cannot mutate an existing key when the provider passes the conditional-write suite.
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

### Automatically overwrite or delete a mismatching non-AVAILABLE object

Rejected because the same physical key can affect multiple logical Versions and automatic repair
cannot prove deletion is safe. RoboLake detects, reports, and stops for operator inspection.

## Related documents

- [Architecture: object key layout](../ARCHITECTURE_V0_1.md#7-object-key-layout)
- [ADR 0002: immutable versions](0002-immutable-dataset-versions.md)
- [ADR 0006: M1 conditional single-PUT slice](0006-m1-conditional-single-put-slice.md)
