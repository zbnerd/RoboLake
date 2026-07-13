# RoboLake M2 Product Brief

## Outcome

M2 extends only RoboLake's upload transfer layer. A robotics engineer can interrupt the upload of a
single file larger than 5,000,000,000 bytes, restart `robolake push`, and reuse provider-confirmed
parts instead of returning to byte zero. The resulting Blob, DatasetVersion, manifest, status, and
pull behavior retain the v0.1.0 meaning.

## User problem

MCAP, video, and sensor files can take hours to cross an unreliable link. A process crash, network
failure, machine restart, or lost response must not discard successfully stored parts. Recovery
must also be safe when completion succeeded but its response was lost, or when another client is
uploading the same content concurrently.

## M2 scope

- deterministically divide a large Blob into at most 10,000 parts;
- persist one provider multipart upload and its intended part plan;
- issue short-lived, length- and SHA-256-bound UploadPart capabilities;
- upload a small rolling window with bounded parallelism;
- reconcile PostgreSQL with paginated provider `ListParts` results;
- replace only missing or mismatching incomplete parts;
- complete directly at the deterministic Blob key using `If-None-Match: *`;
- stream the completed object once to prove its full SHA-256 before `AVAILABLE`;
- converge concurrent publishers without overwriting or deleting a final object; and
- explicitly abort an active workflow while retaining provider stale-upload cleanup as a backstop.

The M1 single-PUT path remains unchanged for files at or below 5,000,000,000 bytes. M2 does not
change canonical manifests, DatasetVersion identity, Blob identity, READY meaning, pull, or the
Linux/macOS filesystem contract.

## Success criteria

1. A synthetic file above the M1 limit reaches `READY`, pulls, and compares byte-for-byte.
2. Restart after arbitrary completed parts transfers only unresolved parts.
3. A lost completion response converges through the deterministic final key.
4. Concurrent same-Blob pushes produce one immutable final object.
5. Wrong parts, a poisoned final key, and unsafe local-file changes fail closed with stable actions.
6. Normal PR CI proves the protocol with small provider-valid multipart objects; a scheduled/manual
   multi-GB profile supplies scale evidence.

Poisoned non-`AVAILABLE` final keys remain outside automatic recovery. Operators use the
[evidence-preserving runbook](M2_POISONED_FINAL_KEY_RUNBOOK.md); the application never overwrites or
deletes the final key.

## Non-goals

M2 does not add pull resume, Range GET, parallel pull, Windows support, authentication,
multi-tenancy, general Blob garbage collection, automatic repair, continuous auditing, Kafka,
Airflow, Iceberg, Parquet, ROS2/MCAP interpretation, LeRobot conversion, frontend, Kubernetes, or
model workflows.

## Product boundary

RoboLake remains a trusted-network modular monolith, not a production multi-tenant SaaS. Multipart
session IDs are routing identifiers, not credentials. Presigned URLs are bearer capabilities and
must never appear in logs or errors. `READY` still attests that publication verification passed; it
does not promise that storage can never corrupt later.
