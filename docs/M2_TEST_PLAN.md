# RoboLake M2 Test Plan

## Test principles

M2 tests prove state and storage semantics, not only happy-path HTTP calls. Unit tests use fake
ports and deterministic clocks; PostgreSQL tests exercise constraints/triggers; provider tests run
against the exact pinned MinIO digest. No test treats ETag as SHA-256 or inspects provider error
bodies in the CLI.

Normal CI uses provider-valid parts just above MinIO's 5 MiB non-final minimum. It never creates
multi-gigabyte artifacts. The >5,000,000,000-byte demonstration is manual/scheduled.

## Level 1: normal PR CI

### Domain and application

- part-size formula and exact boundaries for 5,000,000,001 B, 10 GiB, 100 GiB, 1 TiB, 5 TiB,
  50,000,000,000,000 B, exact multiples, and a one-byte final part;
- reject zero/negative/over-maximum size, more than 10,000 parts, nonconsecutive numbers, overlapping
  ranges, wrong final size, and client-selected part size;
- stable full/part hashing with source identity before/after; inode/size/mtime and invisible-byte
  mutation scenarios;
- all legal/illegal session and part transitions;
- stable error code, action, and CLI exit mapping;
- `ISSUED`/client-reported `UPLOADED` never advance progress; only provider-reconciled VERIFIED
  parts do;
- metrics keep logical/unique/invocation/part/wire meanings separate, including
  `completed_object_bytes`, `verification_read_bytes`, and
  `whole_object_verification_duration`; and
- M1 single-PUT selection/regression at exactly 5,000,000,000 B.

### PostgreSQL integration

- migration from v0.1.0 schema with existing M1 rows and downgrade preflight;
- one active session per Blob under concurrent inserts;
- frozen strategy, Blob link, provider ID, part size/count/offset/size/hash after `IN_PROGRESS`;
- part cannot attach to another session or duplicate number/offset;
- `VERIFIED` requires provider receipt/checksum equality;
- `COMPLETING` requires all planned parts verified;
- `COMPLETED` requires Blob AVAILABLE and verification timestamp;
- terminal sessions cannot mutate;
- direct SQL cannot mutate AVAILABLE Blob/READY Version or invent a mismatching target; and
- no mutable progress counter exists to drift after a crash.

### Pinned MinIO contract

Each test creates isolated keys and aborts/deletes only its own synthetic state:

1. normal multipart upload and full-object verification;
2. interruption after a subset and resume without retransmitting matching parts;
3. exact duplicate part replay;
4. different same-number part replacement before completion;
5. wrong SHA-256 and signed-length rejection;
6. paginated ListParts reconciliation (fake page size plus live provider smoke);
7. lost UploadPart API response with provider part present;
8. lost Complete response through a faulting transport/proxy;
9. HTTP 200 with an embedded Complete error is surfaced by the adapter and never marks the session
   COMPLETED or Blob AVAILABLE;
10. repeated conditional Complete;
11. Abort followed by `NoSuchUpload`;
12. provider-side disappearance and new-session recovery;
13. a 409 with absent final invalidates the old MPU, creates a new ID, and uploads all parts again;
14. concurrent same-Blob session creation and provider MPU races;
15. unguarded Complete demonstrably overwrites in a probe-only negative test;
16. conditional final completion yields exactly one winner;
17. matching final key is adopted only after full-byte verification;
18. mismatching final key is never overwritten or deleted;
19. incomplete loser is explicitly aborted after matching final adoption;
20. stale presigned part URL cannot alter an AVAILABLE final object;
21. provider checksum/composite checksum is not accepted as whole Blob SHA-256; and
22. capability URLs/query canaries, independently exposed provider upload IDs, credentials, and
    absolute paths are absent from logs.

### API and CLI

- same push chooses M1 single PUT below/equal threshold and M2 above it;
- create/resolve, status, reconcile, rolling-window issue, confirm, complete, and permitted abort;
- capability response never exceeds concurrency/hard maximum;
- client never advances more than the window and stops scheduling after one failure;
- Ctrl-C leaves the session resumable rather than aborting it;
- resume reports reused versus newly transferred parts correctly;
- 409/412/provider-unavailable messages are actionable and contain no provider body;
- final full GET mismatch maps to `STORED_OBJECT_MISMATCH`/`CONTACT_OPERATOR`/exit 5;
- M1 push/pull/status/manifest and one-capability pull suites remain unchanged; and
- Linux/macOS filesystem contract tests remain green.

Required PR commands:

```bash
uv sync --locked
docker compose up -d --wait
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run mypy robolake apps scripts
uv run pytest
scripts/demo-v01.sh
```

## Level 2: scheduled/manual multi-GB profile

Run only on a host with at least 20 GB free space for source, provider data, pull staging/output, and
verification overhead. The profile uses deterministic synthetic bytes and a regular file of at
least `5_000_000_001` bytes; it must not use sparse-hole equality as evidence without reading every
byte.

Procedure:

1. record CPU, RAM, filesystem, free disk, OS/kernel, Docker, PostgreSQL, MinIO image/version, and
   RoboLake commit;
2. stream-generate a synthetic robot-like file above the M1 limit;
3. compute canonical manifest and record scan/hash duration and peak RSS;
4. start push with concurrency 4 and upload a known set of parts;
5. forcibly terminate the CLI process without calling abort;
6. record provider ListParts and database status;
7. rerun the identical push and prove earlier part numbers were reused, not retransmitted;
8. complete conditional publication, full-byte verification, and Version finalization;
9. pull through the unchanged sequential M1 contract;
10. compare source/restored size and SHA-256 (and `cmp` where practical);
11. scan captured logs for presigned/capability query parameters and credentials; and
12. publish wall times, hashing throughput, push/finalize/pull durations, peak RSS,
    `newly_transferred_part_bytes`, `reused_provider_part_bytes`, `completed_object_bytes`,
    `verification_read_bytes`, `whole_object_verification_duration`, and measurement limitations.

Invocation metrics do not claim wire bytes. If retries partially transmitted bodies, only external
network telemetry may report that traffic.

Failure of checksum, create-only final publication, resume, byte equality, bounded memory, or secret
redaction is a release blocker. Lack of resources to run the profile is reported, not fabricated.

## Level 3: property and state-machine tests

Use generated Blob sizes and event sequences:

- every accepted plan covers `[0, S)` exactly once, uses `1..N`, and satisfies part limits;
- reconstructing generated expected parts always hashes to the original full digest;
- every illegal session/part transition is rejected by both domain and PostgreSQL;
- replaying prepare/reconcile/confirm/complete/abort is idempotent;
- arbitrary crash points between DB intent, provider call, and DB acknowledgement converge according
  to the failure matrix;
- provider part sets containing absence, duplication, size mismatch, checksum mismatch, and page
  boundaries never produce false progress;
- two completion histories produce at most one final object and one AVAILABLE attestation;
- frozen session parameters never change under concurrent commands;
- an AVAILABLE Blob/READY Version is unchanged under every generated M2 operation; and
- status-derived counts always equal queries over part/Blob state.

Concurrency tests use database barriers rather than sleeps. Provider timing tests use a faulting
HTTP transport/proxy and condition polling, not arbitrary long waits.

## Coverage gates

All new domain/application branches, SQL constraints, error mappings, and provider disagreement rows
must have direct tests. Overall coverage may not regress below the v0.1.0 baseline (~87%), but a
single percentage is not a substitute for the listed state/failure assertions.
