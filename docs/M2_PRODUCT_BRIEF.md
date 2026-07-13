# RoboLake M2 Product Brief

## Outcome

M2 extends only RoboLake's upload transfer layer. A robotics engineer can interrupt the upload of a
single file larger than 5,000,000,000 bytes, restart `robolake push`, and reuse receipt-backed,
provider-verified parts instead of returning to byte zero. The resulting Blob, DatasetVersion,
manifest, status, and pull behavior retain the v0.1.0 meaning.

## User problem

MCAP, video, and sensor files can take hours to cross an unreliable link. A process crash, network
failure, machine restart, or lost response must not discard successfully stored parts. Recovery
must also be safe when completion succeeded but its response was lost, or when another client is
uploading the same content concurrently.

## M2 scope

- deterministically divide a large Blob into at most 10,000 parts;
- persist one generation-numbered provider multipart attempt and its canonical intended part plan;
- keep immutable request replay, one CLI invocation, and one provider-attempt generation as distinct
  identities;
- preserve that resumable session independently from a short-lived fenced admission lease;
- issue short-lived, length- and SHA-256-bound UploadPart capabilities;
- upload a small rolling window with bounded parallelism;
- retain UploadPart response receipts and reconcile them with paginated provider `ListParts` results;
- replace only missing or mismatching incomplete parts;
- accept completion asynchronously, then let a PostgreSQL-claimed server runner complete directly at
  the deterministic Blob key using `If-None-Match: *`;
- keep the runner alive with a separately fenced completion lease while it streams the completed
  object to prove its full SHA-256 before `AVAILABLE`;
- converge concurrent publishers without overwriting or deleting a final object; and
- explicitly abort an active workflow while retaining provider stale-upload cleanup as a backstop.

Provider initiation is explicit: `CREATED` means no request has been sent, `INITIATING` means a
request may be in flight without a durable upload ID, and only `IN_PROGRESS` has an addressable MPU.
An ambiguous initiation terminates that generation instead of guessing or adopting provider state.

The M1 single-PUT path remains unchanged for files at or below 5,000,000,000 bytes. M2 does not
change canonical manifests, DatasetVersion identity, Blob identity, READY meaning, pull, or the
Linux/macOS filesystem contract.

## Success criteria

1. A synthetic file above the M1 limit reaches `READY`, pulls, and compares byte-for-byte.
2. Restart after arbitrary completed parts transfers only unresolved parts.
3. A lost completion response converges through the deterministic final key.
4. Concurrent same-Blob pushes produce one immutable final object.
5. Wrong parts, a poisoned final key, and unsafe local-file changes fail closed with stable actions.
6. Abandoned invocations release admission capacity without discarding provider-confirmed parts.
7. A lost or long-running completion request returns promptly, remains server-owned, and survives
   runner takeover without trusting stale work.
8. Terminal provider attempts create a new immutable session generation without rebinding an old
   idempotency record or reusing old parts.
9. Normal PR CI proves the protocol with small provider-valid multipart objects; a scheduled/manual
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
session IDs, request IDs, invocation IDs, and lease coordinates are routing/idempotency/fencing
values, not credentials. Presigned URLs are bearer capabilities and must never appear in logs or
errors. `READY` still attests that publication verification passed; it does not promise that storage
can never corrupt later.

No Kafka, Celery, or external queue is introduced. `COMPLETING` rows in PostgreSQL are the durable
work registry; a bounded runner using the same modular-monolith code and image claims them and the
CLI observes progress through short status polls.

The first M2 deployment is offline: every v0.1.0 server is drained and stopped before migration and
no mixed-version rolling rollout is supported.
