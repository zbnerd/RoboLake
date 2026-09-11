# RoboLake v0.1.0 Security Model Summary

## Security objective

RoboLake prioritizes dataset integrity: every downloaded logical file must match the sealed
manifest, and no manifest path may read or write outside the selected source or destination. It
also avoids exposing dataset bytes, credentials, presigned query strings, or local absolute paths
through server state and normal diagnostics.

This summary is not a replacement for the [full threat model](THREAT_MODEL_V0_1.md).

## Trust boundary

v0.1.0 has no authentication, authorization, or tenant isolation. Anyone who can reach the API can
create registry resources and obtain transfer capabilities. Run it only on a trusted network. TLS,
network policy, database backup, object-store durability, credential rotation, and host security
are operator responsibilities outside the local Compose environment.

Presigned URLs are short-lived bearer capabilities. The CLI receives no permanent S3 credential,
but any holder may use an unexpired URL for its signed operation.

## Principal controls

- Safe paths: UTF-8 NFC relative POSIX paths; reject traversal, controls, absolute paths,
  normalization/case collisions, and file/ancestor collisions.
- Safe source reads: Linux/macOS descriptor-relative traversal, `O_NOFOLLOW`, regular files only,
  and identity checks before/after scan and upload.
- Immutable content: canonical manifest SHA-256, content-addressed Blob keys, sealed Versions, and
  PostgreSQL immutability/state triggers.
- Create-only writes: exact key/method/length/checksum plus `If-None-Match: *`; existing objects are
  reconciled and never overwritten or automatically deleted.
- Integrity verification: provider system checksum and size, streamed full-GET fallback when the
  system checksum is absent, and mandatory pull-time SHA-256.
- Safe destination: private staging, exclusive non-following part files, file fsync, and atomic
  no-replace publication on supported filesystems.
- Safe failure: structured error codes and caller actions without provider response bodies,
  credentials, local source roots, or presigned query strings.

## Residual risks

- Internet-facing or shared untrusted deployment is unsafe.
- A compromised API process can operate on its configured database and Blob prefix.
- READY and AVAILABLE attest integrity at publication, not continuously afterward.
- No malware scanning, file-format validation, object lock, automatic repair, or disaster-recovery
  automation is provided.
- Atomic visibility does not claim power-loss durability.
- Windows and SMB filesystem behavior are not supported.

## Deployment minimums

- Restrict API, PostgreSQL, MinIO API, and MinIO console exposure by network policy.
- Use TLS whenever traffic leaves the trusted local network.
- Replace synthetic `.env.example` credentials and keep real values in ignored/managed secrets.
- Limit the API S3 principal to the configured bucket and required Blob-prefix operations.
- Back up PostgreSQL and object storage as one logical service; neither alone preserves the registry.
- Keep the pinned MinIO conditional-write/checksum integration suite passing before changing
  provider or image.

For incident expectations and the complete threat register, use
[THREAT_MODEL_V0_1.md](THREAT_MODEL_V0_1.md).
