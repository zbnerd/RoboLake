# RoboLake v0.1 Product Brief

- **Status:** Accepted for v0.1
- **Date:** 2026-07-10

## Problem

Robotics researchers keep teleoperation datasets on local workstations or shared file storage and
manually copy selected directories to a training environment. A large copy can stop halfway, an
operator can copy the same bytes more than once, and the destination may not retain a reliable
record of what was transferred or whether it still matches the source.

RoboLake v0.1 addresses only this transfer and registry problem. The contents of MCAP, video,
sensor, and other files are opaque bytes. No downstream preprocessing or training behavior is
assumed.

## Users

- **Dataset producer/researcher:** selects a local directory, transfers it, checks progress, and
  later retrieves the exact version.
- **RoboLake operator:** runs the API, PostgreSQL, and S3-compatible storage; diagnoses failed or
  abandoned transfers.

Authentication and multi-tenancy are intentionally absent in v0.1, so deployment is limited to a
trusted environment.

## Product outcome

A researcher can run a CLI workflow that:

1. scans a local directory without following symbolic links;
2. emits a deterministic manifest of logical relative paths, sizes, media types, and SHA-256
   digests;
3. registers a dataset and a content-fixed version;
4. transfers missing blobs directly to S3-compatible storage with resumable multipart upload;
5. asks RoboLake to verify stored bytes before marking the version `READY`; and
6. downloads a `READY` version into a safe destination and reconstructs its manifest paths.

The registry answers which immutable version owns each logical path and which content-addressed
blob stores its bytes. It does not interpret the bytes.

## In scope

- Typer CLI for scan, register, upload, status, verify/finalize, and download
- FastAPI control plane and PostgreSQL registry
- MinIO-backed local development through the S3 API
- Presigned single-part and multipart transfer, retry, and resume
- Blob reuse by `(sha256, size_bytes)`
- Full-file SHA-256 verification after upload and download
- Explicit version and upload-session state transitions
- Cleanup of abandoned multipart uploads

## Explicit non-goals

Kafka, Airflow, Iceberg, Parquet conversion, ROS2 integration, MCAP parsing, LeRobot conversion,
model training, model registry, authentication, multi-tenancy, a frontend, Kubernetes, and
microservices are excluded. A new capability requires an issue and an ADR; architecture appearance
alone is not a reason to add technology.

## Success criteria

- Two scans of an unchanged directory produce byte-identical canonical manifests and the same
  manifest SHA-256 regardless of traversal order.
- A killed upload resumes without retransmitting parts already confirmed by object storage.
- Retrying dataset creation, version registration, part acknowledgement, completion, or finalization
  produces the same result rather than duplicate resources.
- `READY` is impossible until every entry resolves to a size- and SHA-256-verified blob.
- A downloaded version has the same relative paths, file sizes, and SHA-256 values as its manifest.
- Unsafe paths, symlinks, changed-during-scan files, and destination escapes fail closed with useful
  CLI messages.
- A manual release test transfers and reconstructs synthetic data containing at least one 6 GiB
  file through local MinIO.
- API processes never proxy dataset file bodies during upload or download.

## Product principles

- Build one end-to-end slice at a time and keep the CLI usable at every milestone.
- Treat object storage as the byte store and PostgreSQL as the source of searchable metadata and
  workflow state.
- Separate user-visible paths from physical object keys.
- Prefer idempotent commands and explicit recovery over hidden background behavior.
- Make immutability a database- and domain-enforced invariant, not a UI convention.

## Assumptions and risks

- The API can reach PostgreSQL and object storage over a reliable local network; the CLI can reach
  the API and the object-storage endpoint embedded in presigned URLs.
- Very long server-side SHA-256 verification may exceed an infrastructure HTTP timeout. v0.1 keeps
  verification synchronous and retryable; a durable job mechanism is considered only after this is
  observed.
- Dataset file counts, aggregate sizes, and available bandwidth are not yet measured. Interfaces
  must paginate and stream where practical, while limits remain configurable rather than invented.
- Presigned URLs are bearer capabilities. Short lifetimes and log redaction reduce, but do not
  replace, the deployment boundary that authentication would provide.

## Related documents

- [User stories](USER_STORIES.md)
- [Milestone roadmap](ROADMAP.md)
- [Architecture](ARCHITECTURE_V0_1.md)
- [Threat model](THREAT_MODEL_V0_1.md)
- [Architecture decision records](adr/)
