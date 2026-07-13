# Known Limitations — RoboLake v0.1.0

These limitations are part of the v0.1.0 product boundary, not undocumented defects.

## Transfer limits

- Each file must be at most 5,000,000,000 bytes. Oversized input is rejected before persistent
  DatasetVersion, UploadSession, or object state is created.
- Push is sequential per unique Blob and resumes only at verified whole-file boundaries.
- A failed single PUT restarts that file from byte zero. Multipart and within-file resume are M2.
- Pull is sequential, has no Range retry or resume, and restarts the complete staging operation after
  failure.
- Exact wire bytes and retry bytes are not product metrics. `created_blob_bytes` describes logical
  size of newly created objects, not network traffic.

## Snapshot limits

- A manifest supports at most 100,000 regular-file entries and 64 MiB canonical JSON.
- A relative path is at most 1,024 UTF-8 bytes and each segment at most 255 bytes.
- Nested empty directories, symlinks, hard-link identity, device files, permissions, ownership,
  timestamps, ACLs, and extended attributes are not represented or restored.
- NFC and case-fold collisions are rejected even on case-sensitive Linux filesystems so snapshots
  remain reconstructable across supported Linux/macOS environments.

## Platform and durability limits

- Runtime filesystem support is Linux and macOS only. Windows, SMB edge cases, and network
  filesystem atomicity are not claimed.
- Pull guarantees atomic visibility through platform no-replace rename; it does not guarantee
  directory fsync or survival of every power-loss window.
- The pinned MinIO image is the verified S3-compatible provider. Other providers must demonstrate
  equivalent conditional-write and checksum semantics.
- v0.1.0 is distributed as a GitHub source checkout with Docker Compose. The locally buildable wheel
  validates package metadata and the CLI, but omits Alembic/Compose deployment assets and is not a
  supported standalone server installation or PyPI release.

## Integrity limits

- AVAILABLE means a Blob passed the verification contract before publication.
- READY means every referenced Blob was AVAILABLE when the Version was published.
- Neither state is continuous storage scrubbing. Later corruption is detected on pull; no background
  auditor is included.
- A mismatching object at a content-addressed key stops with `CONTACT_OPERATOR`. The application
  does not overwrite, delete, quarantine, or repair it.
- v0.1.0 has no Blob garbage collection or DatasetVersion deletion workflow.

## Security and product limits

- There is no authentication, authorization, multi-tenancy, per-user audit, or capability
  revocation. Use only in a trusted network.
- TLS and external network controls are deployment responsibilities outside local Compose.
- RoboLake treats file bytes as opaque. It does not parse MCAP, ROS2, video, sensor schemas, or
  dataset semantics.
- It does not provide preprocessing, training, model registry, Kafka, Airflow, Iceberg, Parquet,
  frontend, Kubernetes, or microservices.

## Known tooling warning

The test suite emits one upstream FastAPI/Starlette TestClient deprecation warning recommending a
future `httpx2` test-client dependency. Runtime push/pull does not use this import. It is classified
as a follow-up dependency issue, not a v0.1.0 release blocker, and remains visible in test output.

See the [roadmap](ROADMAP.md) for milestone ownership and the
[security summary](SECURITY_MODEL_SUMMARY.md) for accepted residual risk.
