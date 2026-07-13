# Architecture Decision Record Index

Accepted ADRs 0001–0007 define the released RoboLake v0.1.0 baseline. ADRs 0008–0010 are proposed by
the unmerged M2 design PR and become Accepted only when that PR is merged. Accepted decisions must
be revised through a new ADR rather than silently changed in implementation or deployment
configuration; correcting a Proposed ADR in its original review does not require another ADR.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](adr/0001-v01-scope-and-non-goals.md) | Solve verified robot dataset transfer only; exclude speculative data-platform and ML infrastructure. | Accepted |
| [0002](adr/0002-immutable-dataset-versions.md) | Seal manifest identity at registration; READY is terminal; version numbers follow registration order. | Accepted |
| [0003](adr/0003-object-storage-and-content-addressed-blobs.md) | Store raw bytes by SHA-256 in object storage and keep logical paths/metadata in PostgreSQL. | Accepted |
| [0004](adr/0004-resumable-upload-strategy.md) | Use API-controlled presigned direct transfer; M1 is single PUT and M2 owns multipart. | Accepted |
| [0005](adr/0005-modular-monolith.md) | Keep domain/application framework-free inside one Python deployable and shared CLI package. | Accepted |
| [0006](adr/0006-m1-conditional-single-put-slice.md) | Use create-only conditional PUT, whole-Blob resume, reconciliation, and one-at-a-time pull capability. | Accepted |
| [0007](adr/0007-failed-publication-and-manual-repair.md) | Detect, report, and stop on poisoned objects; do not guess, overwrite, delete, or auto-repair. | Accepted |
| [0008](adr/0008-multipart-session-model.md) | Freeze deterministic multipart plans, fence invocation leases, and reconcile provider parts inside one resumable Blob session. | Proposed |
| [0009](adr/0009-multipart-final-publication.md) | Complete directly to the final key create-only and require full-byte SHA-256 before AVAILABLE. | Proposed |
| [0010](adr/0010-multipart-reconciliation.md) | Resolve lost responses, NoSuchUpload, lease takeover, abort, and concurrency from provider facts plus deterministic identity. | Proposed |

## Reading order

New contributors should read 0001, 0002, 0003, and 0006 first. Together they define product scope,
snapshot identity, physical content identity, and the concrete M1 transfer contract. Read 0007
before changing error or recovery behavior, and 0005 before changing dependency boundaries.

For M2 implementation, read 0008, 0009, and 0010 in order after the accepted M1 set. They supersede
only ADR 0004's future-M2 assumptions and leave its historical M1 decisions intact.

Supporting documents:

- [Architecture overview](ARCHITECTURE_OVERVIEW.md)
- [Detailed v0.1 architecture](ARCHITECTURE_V0_1.md)
- [Security summary](SECURITY_MODEL_SUMMARY.md)
- [Full threat model](THREAT_MODEL_V0_1.md)
- [Known v0.1.0 limitations](KNOWN_LIMITATIONS_V0.1.0.md)
- [M2 product brief](M2_PRODUCT_BRIEF.md)
- [M2 multipart architecture](M2_MULTIPART_ARCHITECTURE.md)
- [M2 provider probes](M2_PROVIDER_PROBES.md)
- [M2 failure matrix](M2_FAILURE_AND_RECONCILIATION_MATRIX.md)
- [M2 security model](M2_SECURITY_MODEL.md)
- [M2 test plan](M2_TEST_PLAN.md)
- [M2 migration and rollback](M2_MIGRATION_AND_ROLLBACK.md)
- [M2 poisoned final-key runbook](M2_POISONED_FINAL_KEY_RUNBOOK.md)
- [M2 implementation plan](M2_IMPLEMENTATION_PLAN.md)
