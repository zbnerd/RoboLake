# ADR 0007: Failed Publication and Manual Repair Boundary

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

Upload workflow can fail without invalidating the immutable manifest. Some failures are safe to
retry, while a mismatching object already stored at a content-addressed key cannot be automatically
deleted or replaced without understanding its references and blast radius.

## Decision

Manifest/entry identity is immutable immediately after registration in every lifecycle state.
`FAILED` describes publication workflow, not a mutable or discarded DatasetVersion. The same
Version may return through `FAILED -> UPLOADING -> VERIFYING -> READY` only with its original
manifest and expected Blob hashes.

M1 follows **detect → report → stop**:

- network/database errors, provider checksum rejection with no object, and `409` with no visible
  object leave progress non-terminal and derive a retry action;
- a mismatching existing content key records `STORED_OBJECT_MISMATCH`, derives
  `CONTACT_OPERATOR`, and never triggers application overwrite/delete;
- M1 exposes no repair CLI, admin API, RBAC, deletion policy, or audit workflow;
- after an operator independently inspects/removes a poisoned non-AVAILABLE object, rerunning push
  resumes the same Version rather than creating another manifest identity.

API errors use stable symbolic causes. `next_action` is derived from state/cause and is not stored;
CLI exit codes remain broad automation categories. Error messages contain only safe logical
identifiers and action, never provider bodies, credentials, absolute paths, or presigned queries.

An `AVAILABLE` Blob is a reusable verification attestation. `READY` means every referenced Blob
passed the contract before publication; it is terminal but not a continuous storage-integrity
guarantee. Pull rehashes actual bytes and reports later corruption without regressing READY or
adding a persistent CORRUPTED state in M1.

## Consequences

- Failures do not create v2/v3 merely because a workflow attempt failed.
- A non-READY older Version remains valid when changed local content registers the next number.
- Poisoned storage may block publication until operator action; this is intentional scope control.
- Background auditing, automatic repair, deletion, RBAC, and operator audit trails require later
  observed needs and ADRs.

## Alternatives considered

### Automatically overwrite or delete non-AVAILABLE objects

Rejected because content-addressed physical state may affect more than the initiating Version.

### Add recoverable/fatal Version states

Rejected because one FAILED state plus stable cause and derived action expresses M1 semantics
without multiplying lifecycle states.

### Add a repair command in M1

Rejected because safe repair immediately requires authorization, audit, deletion, and recovery
policy outside the milestone.

## Related documents

- [ADR 0002: immutable DatasetVersions](0002-immutable-dataset-versions.md)
- [ADR 0003: content-addressed Blobs](0003-object-storage-and-content-addressed-blobs.md)
- [ADR 0006: conditional single-PUT slice](0006-m1-conditional-single-put-slice.md)
