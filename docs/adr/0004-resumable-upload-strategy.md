# ADR 0004: Presigned Resumable Upload Strategy

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

The transfer path must handle multi-gigabyte MCAP, video, and sensor files, unreliable connections,
and process interruption. The API must retain registry and lifecycle control without becoming the
file-byte bottleneck or distributing permanent storage credentials to researcher machines.

## Decision

Use an API-controlled, presigned direct-transfer strategy. M1 first proves a bounded complete slice
with one create-only `PutObject` per Blob and rejects files above 5,000,000,000 bytes before any
persistent mutation. M2 implements the final v0.1 large-file strategy:

- files below 64 MiB use a presigned single `PutObject`;
- files at or above 64 MiB use S3 multipart upload with a 64 MiB default part;
- part size grows as needed to stay within 10,000 parts and provider limits;
- API creates, lists, completes, and aborts provider uploads;
- CLI uploads bytes directly using short-lived URLs for an exact key/method/part;
- API persists `UploadSession` and `UploadPart` state and reconciles resume with paginated
  `ListParts`;
- CLI revalidates the sealed source manifest before resuming and retransmits only absent, mismatched,
  or uncertain parts;
- final publication uses a create-only precondition and completion/`NoSuchUpload` ambiguity is
  resolved through the deterministic key and the shared verification contract;
- URLs expire after 15 minutes, application sessions after 72 idle hours, and provider stale uploads
  after seven days.

Part ETags are opaque receipts. Publication never treats ETag or caller metadata as full-file
identity. It accepts exact provider system full-object checksum/size when the provider passes the
contract suite and falls back to a streamed full GET when that checksum is absent. Explicit
application cleanup plus provider stale-upload cleanup handles multipart orphan windows.

AWS documents the 10,000-part and 5 MiB–5 GiB limits
([multipart limits](https://docs.aws.amazon.com/AmazonS3/latest/userguide/qfacts.html)), presigned
upload capabilities
([presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)),
and the need to complete or abort multipart uploads
([multipart overview](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)).

## Consequences

- Dataset bytes do not consume API request bandwidth or memory.
- CLI stores no permanent object-storage credential.
- Resume survives CLI/API restart because PostgreSQL and object storage are authoritative.
- The control plane needs URL renewal, part receipts, provider reconciliation, cleanup, and
  idempotent completion logic.
- A URL holder can replay its permitted operation before expiry; v0.1 relies on a trusted network,
  short lifetime, narrow signing, and log redaction.
- Providers without a retrievable system full-object SHA-256 pay one fallback read; each supported
  provider must pass the same contract suite.

## Alternatives considered

### A. API proxies all bytes

Rejected because it doubles the data path through the API and makes long transfers, restart, timeout,
and scaling behavior an API concern.

### B. CLI uses permanent object-storage credentials

Rejected because credential distribution, least-privilege policy, rotation, and revocation are larger
and riskier than a narrow presigning control plane.

### Presign one PUT for every file as the final v0.1 strategy

Rejected for large files because a failed multi-gigabyte PUT restarts from byte zero and has no
portable multipart resume point. ADR 0006 authorizes it only for the bounded M1 vertical slice.

### Add a queue and background upload workers

Rejected because workers would still need access to local source bytes or proxy them, and no observed
need justifies queue infrastructure.

## Related documents

- [Architecture: retry and resume](../ARCHITECTURE_V0_1.md#11-retry-url-renewal-and-resume-semantics)
- [Threat model: presigned capability policy](../THREAT_MODEL_V0_1.md#8-presigned-capability-policy)
- [ADR 0006: M1 conditional single-PUT slice](0006-m1-conditional-single-put-slice.md)
