# Changelog

All notable changes to RoboLake are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning.

## [Unreleased]

No user-visible changes.

## [0.1.0] - 2026-07-13

### Added

- Deterministic regular-file-tree scanning with streamed SHA-256 and portable Linux/macOS path
  validation.
- Immutable Dataset and DatasetVersion registry backed by PostgreSQL constraints and triggers.
- Globally deduplicated content-addressed Blobs stored through the S3-compatible API.
- Create-only presigned single-PUT transfers with provider-checksum reconciliation and full-GET
  fallback when the provider system checksum is unavailable.
- File-level interrupted-push recovery, immutable READY publication, status, and canonical manifest
  commands.
- One-capability-at-a-time verified pull with private staging and atomic no-replace publication.
- Structured API error codes, derived caller actions, stable CLI exit classes, and URL redaction.
- Synthetic demo, PostgreSQL/MinIO integration tests, and Linux/macOS filesystem-contract CI.

### Security

- Symlink, special-file, path-traversal, Unicode/case collision, and source mutation defenses.
- Signed expected length, SHA-256, key, method, and `If-None-Match: *` for Blob creation.
- Automatic repair and object deletion intentionally omitted when stored content cannot be proven.

[Unreleased]: https://github.com/zbnerd/RoboLake/compare/v0.1.0...develop
[0.1.0]: https://github.com/zbnerd/RoboLake/releases/tag/v0.1.0
