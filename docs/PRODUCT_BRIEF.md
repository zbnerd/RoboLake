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
2. emits a minimal deterministic manifest of logical relative paths, sizes, and SHA-256 digests;
3. registers a dataset and a content-fixed version;
4. transfers missing blobs directly to S3-compatible storage, using M1 create-only single PUTs and
   M2 multipart resume for larger files;
5. applies one provider-attested SHA-256/size verification contract before marking a Blob
   `AVAILABLE`; and
6. downloads a `READY` version into a safe destination and reconstructs its manifest paths.

The registry answers which immutable version owns each logical path and which content-addressed
blob stores its bytes. It does not interpret the bytes.

## In scope

- Typer CLI for scan, register, upload, status, verify/finalize, and download
- FastAPI control plane and PostgreSQL registry
- MinIO-backed local development through the S3 API
- Create-only presigned single PUT and file-level resume in M1; multipart/within-file resume in M2
- Blob reuse by `(sha256, size_bytes)`
- M1 provider system SHA-256/size verification with full-GET fallback; M2 mandatory full-stream
  SHA-256 verification after multipart completion; download always hashes materialized bytes
- Explicit version and upload-session state transitions
- Fenced admission-lease expiry plus explicit known-MPU abort and provider stale-upload cleanup

## Explicit non-goals

Kafka, Airflow, Iceberg, Parquet conversion, ROS2 integration, MCAP parsing, LeRobot conversion,
model training, model registry, authentication, multi-tenancy, a frontend, Kubernetes, and
microservices are excluded. A new capability requires an issue and an ADR; architecture appearance
alone is not a reason to add technology.

## Success criteria

- Two scans of an unchanged directory produce byte-identical canonical manifests and the same
  manifest SHA-256 regardless of traversal order.
- A killed M1 upload resumes without retransmitting whole Blobs already confirmed by the verification
  contract; M2 adds within-file multipart resume.
- Retrying dataset creation, version registration, part acknowledgement, completion, or finalization
  produces the same result rather than duplicate resources.
- `READY` is impossible until every entry references an immutable `AVAILABLE` Blob. It is not a
  continuous storage-integrity guarantee.
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
- Prefer correctness over convenience and use detect-report-stop when automatic repair cannot be
  proven safe.
- Keep canonical identity minimal; reproducible presentation/enrichment metadata stays outside it.

## Assumptions and risks

- The API can reach PostgreSQL and object storage over a reliable local network; the CLI can reach
  the API and the object-storage endpoint embedded in presigned URLs.
- Object storage is trusted to provide its documented system-checksum semantics. The exact pinned
  MinIO image is a compatibility gate. M1 falls back to streamed GET when full-object system SHA-256
  is absent; M2 always streams the completed multipart object because its provider checksum is
  composite.
- Manifest-validity limits are fixed protocol constants. Transfer TTLs/timeouts and provider limits
  are operational settings bounded by protocol/provider maxima.
- Local filesystem behavior is supported on Linux/macOS. Windows runtime, SMB edge cases, and
  power-loss durability are not v0.1 claims.
- Presigned URLs are bearer capabilities. Short lifetimes and log redaction reduce, but do not
  replace, the deployment boundary that authentication would provide.

## Related documents

- [User stories](USER_STORIES.md)
- [Milestone roadmap](ROADMAP.md)
- [Architecture](ARCHITECTURE_V0_1.md)
- [Threat model](THREAT_MODEL_V0_1.md)
- [Architecture decision records](adr/)
