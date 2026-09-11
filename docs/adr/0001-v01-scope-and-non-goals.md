# ADR 0001: v0.1 Scope and Non-Goals

- **Status:** Accepted
- **Date:** 2026-07-10

## Context

The observed problem is manual movement of robot teleoperation dataset directories from local or
shared storage to a training environment. Transfers can fail, repeat bytes, lose provenance, or be
difficult to inspect. The downstream preprocessing and training workflow has not been observed.

Calling RoboLake a data lake can encourage broad platform work before its users and constraints are
known. That would delay validation of the transfer problem and introduce components without an
evidence-backed requirement.

## Decision

RoboLake v0.1 will provide one complete workflow:

1. scan a local dataset directory;
2. create a deterministic manifest of opaque regular files;
3. register a dataset and content-fixed version;
4. upload missing blobs reliably to S3-compatible object storage;
5. resume interrupted uploads;
6. verify size and SHA-256 through a documented storage contract before publication; and
7. download a `READY` version and reconstruct its logical file tree.

The CLI is a first-class interface. PostgreSQL stores searchable registry/workflow metadata; object
storage stores raw file bytes. The API and CLI do not interpret file formats.

The canonical manifest is deliberately minimal: schema version plus normalized regular-file paths,
sizes, and SHA-256 values. Suffix-derived media/format hints, nested empty directories, permissions,
ownership, timestamps, hard links, extended attributes, symlinks, and special files are excluded
from snapshot identity. M1 contract-tests Linux/macOS; Windows/SMB behavior is not claimed.

The following are explicit non-goals unless later evidence, an issue, and an ADR require them:

- Kafka, Airflow, Iceberg, or Parquet conversion
- ROS2 runtime integration, MCAP semantic parsing, or LeRobot conversion
- model training or model registry
- authentication, authorization, or multi-tenancy
- frontend/dashboard
- Kubernetes or microservices

No generic plugin system, event bus, workflow engine, or downstream schema will be added “for later.”

## Consequences

- The team can validate an end-to-end transfer with a small modular monolith.
- Files such as MCAP and video are supported as bytes, not as semantic formats.
- v0.1 is safe only inside a trusted deployment boundary because it has no authentication.
- Empty directories, filesystem metadata, and downstream lineage are not represented.
- Canonical manifest validity is deployment-independent; operational transfer limits may vary only
  within protocol/provider maxima.
- Any next scope must come from observed usage after v0.1, not from assumptions about training.

## Alternatives considered

### Build a general robotics data platform

Rejected because unobserved ingestion, conversion, orchestration, search, and training requirements
would dominate the first release.

### Build only a copy command

Rejected because it would not solve provenance, immutable version identity, resumability, deduplication,
or verified reconstruction.

### Integrate with the training workflow now

Rejected because that workflow is unknown. Inventing it would contradict the observed-problem-first
principle.

## Related documents

- [Product brief](../PRODUCT_BRIEF.md)
- [User stories](../USER_STORIES.md)
- [Roadmap](../ROADMAP.md)
