# Architecture Decision Record Index

The ADRs below define RoboLake v0.1.0. All are accepted and must be revised through a new ADR rather
than silently changed in implementation or deployment configuration.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](adr/0001-v01-scope-and-non-goals.md) | Solve verified robot dataset transfer only; exclude speculative data-platform and ML infrastructure. | Accepted |
| [0002](adr/0002-immutable-dataset-versions.md) | Seal manifest identity at registration; READY is terminal; version numbers follow registration order. | Accepted |
| [0003](adr/0003-object-storage-and-content-addressed-blobs.md) | Store raw bytes by SHA-256 in object storage and keep logical paths/metadata in PostgreSQL. | Accepted |
| [0004](adr/0004-resumable-upload-strategy.md) | Use API-controlled presigned direct transfer; M1 is single PUT and M2 owns multipart. | Accepted |
| [0005](adr/0005-modular-monolith.md) | Keep domain/application framework-free inside one Python deployable and shared CLI package. | Accepted |
| [0006](adr/0006-m1-conditional-single-put-slice.md) | Use create-only conditional PUT, whole-Blob resume, reconciliation, and one-at-a-time pull capability. | Accepted |
| [0007](adr/0007-failed-publication-and-manual-repair.md) | Detect, report, and stop on poisoned objects; do not guess, overwrite, delete, or auto-repair. | Accepted |

## Reading order

New contributors should read 0001, 0002, 0003, and 0006 first. Together they define product scope,
snapshot identity, physical content identity, and the concrete M1 transfer contract. Read 0007
before changing error or recovery behavior, and 0005 before changing dependency boundaries.

Supporting documents:

- [Architecture overview](ARCHITECTURE_OVERVIEW.md)
- [Detailed v0.1 architecture](ARCHITECTURE_V0_1.md)
- [Security summary](SECURITY_MODEL_SUMMARY.md)
- [Full threat model](THREAT_MODEL_V0_1.md)
- [Known v0.1.0 limitations](KNOWN_LIMITATIONS_V0.1.0.md)
