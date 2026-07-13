# RoboLake Architecture Overview

## Purpose

RoboLake v0.1.0 is a modular monolith for registering and transferring opaque regular-file-tree
snapshots. It is not a file-format processor or training orchestrator. Its unit of identity is a
canonical manifest; its physical storage unit is an immutable SHA-256 Blob.

## Components

| Component | Responsibility |
| --- | --- |
| Typer CLI | Scan local files, orchestrate push/pull, stream bytes, render safe progress/errors. |
| FastAPI API | Validate control requests, issue short-lived capabilities, coordinate registry and transfer state. |
| Domain | Define identifiers, canonical manifest, lifecycle transitions, immutable records, and exceptions. |
| Application | Implement registry, push, pull, reconciliation, status, and ports without framework imports. |
| PostgreSQL | Store Dataset lineage, sealed Versions, logical entries, Blob attestations, sessions, and idempotency. |
| MinIO/S3 | Store opaque immutable bytes under deterministic SHA-256 object keys. |

The domain and application layers do not import FastAPI, Typer, SQLAlchemy, or the S3 SDK. See
[ADR 0005](adr/0005-modular-monolith.md).

## Identity layers

```text
Dataset UUID                  user-facing lineage container
DatasetVersion UUID           immutable logical snapshot resource
Manifest SHA-256              dataset-scoped snapshot fingerprint
Blob SHA-256                  global physical byte identity
DatasetEntry relative_path    logical file location inside one Version
```

`<dataset>@vN` is a human reference assigned by manifest registration order. Two Datasets may have
different Version UUIDs for the same content while sharing physical Blobs.

## Push flow

1. The CLI safely scans only regular files and streams SHA-256 calculation.
2. It builds canonical JSON ordered by normalized path UTF-8 bytes.
3. The API atomically registers and seals DatasetVersion entries in PostgreSQL.
4. For each unique digest, the API reuses an AVAILABLE Blob or issues one exact create-only PUT.
5. The CLI streams bytes directly to object storage without permanent credentials.
6. The API reconciles object size and provider system SHA-256, falling back to streamed GET when
   the provider checksum is absent.
7. PostgreSQL permits READY only when every referenced Blob is AVAILABLE.

Repeating push for the same manifest converges on the same Version. Completed Blobs are not sent
again. A changed manifest creates the next registration-order version.

## Pull flow

1. The CLI resolves a READY Version and independently validates canonical manifest bytes/totals.
2. It creates a private sibling staging directory.
3. The API returns one manifest entry and one fresh presigned GET URL for the requested ordinal.
4. The CLI streams the object into an exclusive part file, verifies size/SHA-256, fsyncs, and renames
   it inside staging.
5. After every manifest entry is present, the complete staging tree is atomically published without
   replacing an existing destination.

## Persistence invariants

PostgreSQL constraints and triggers enforce dataset-scoped manifest uniqueness, version-number
uniqueness, contiguous immutable manifest ordinals, immutable sealed content, legal lifecycle
transitions, one active UploadSession per Blob, immutable AVAILABLE Blobs, and terminal READY
Versions. The migration is [20260711_0002](../migrations/versions/20260711_0002_m1_registry.py).

## Detailed references

- [Full architecture](ARCHITECTURE_V0_1.md)
- [ADR index](ADR_INDEX.md)
- [Product brief](PRODUCT_BRIEF.md)
- [Security summary](SECURITY_MODEL_SUMMARY.md)
