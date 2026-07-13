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

- part-size formula and exact boundaries for 5,000,000,001 B, 10 GiB, 100 GiB, 1 TiB,
  5,000,000,000,000 B, exact multiples, and a one-byte final part; reject one byte above the
  portable maximum;
- schema-v1 canonical part-plan golden bytes/hash, stable field/part ordering, and rejection of
  whitespace, trailing newline, floats, booleans, duplicate/unknown fields, uppercase digest, and
  mutation of every identity field;
- reject zero/negative/over-maximum size, more than 10,000 parts, nonconsecutive numbers, overlapping
  ranges, wrong final size, and client-selected part size;
- stable full/part hashing with source identity before/after; inode/size/mtime and invisible-byte
  mutation scenarios;
- immutable `request_id` replay, distinct `invocation_id`, monotonic Blob-scoped generation, and
  concurrent resolver convergence;
- all legal/illegal session and part transitions, including `CREATED -> INITIATING -> IN_PROGRESS`,
  `CREATED -> CANCELLED`, and terminal initiation ambiguity;
- stable error code, action, and CLI exit mapping;
- capability issuance never advances progress; response-receipt-backed and ListParts-matching
  `VERIFIED` parts do;
- a ListParts match without a complete stored UploadPart response ETag/checksum is re-uploaded and
  never supplied directly to Complete;
- `COMPLETING -> IN_PROGRESS` succeeds only for final-absent, non-409, same-MPU structurally valid
  partial reconciliation;
- 409/`NoSuchUpload` with no final terminates the old attempt and creates a new all-PENDING plan;
- completion acceptance returns 202, releases upload admission, and remains durable across client
  disconnect; a same-artifact runner owns provider Complete/full verification;
- completion lease heartbeat/takeover fences stale runner results and restarts interrupted full
  verification from byte zero;
- metrics keep logical/unique/invocation/part/wire meanings separate, including
  disjoint newly-transferred/reused/reconciled attribution, `completed_object_bytes`,
  `verification_read_bytes`, and
  `whole_object_verification_duration`; and
- M1 single-PUT selection/regression at exactly 5,000,000,000 B.

### PostgreSQL integration

- migration from v0.1.0 schema with existing M1 rows and downgrade preflight;
- one active generation per Blob under concurrent inserts plus unique positive
  `(blob_id, session_generation)`;
- immutable M2 request bindings never rebind, same acquire request replays one lease result, and a
  terminal generation requires a new request/new generation;
- frozen strategy, Blob link, generation, provider ID, and part size/count/offset/size/hash from
  committed `CREATED` registration;
- part cannot attach to another session or duplicate number/offset;
- different part numbers may carry identical bytes, expected digests, provider checksums, and ETags;
- duplicate part number and overlapping/gapped ranges remain invalid;
- `VERIFIED` requires stored response ETag/checksum plus equal listed ETag/checksum and listed size;
- `COMPLETING/PARTS_READY` requires all planned parts receipt-backed/verified; `FINAL_PRESENT`
  requires a fresh final-key observation and may not falsify incomplete part state;
- guarded `COMPLETING -> IN_PROGRESS` requires recorded same-MPU reconciliation facts, including the
  special vanished-`FINAL_PRESENT` requeue contract;
- `COMPLETED` requires Blob AVAILABLE and structurally consistent immutable verification method,
  digest, size, read bytes, completion time, and verifier version;
- PostgreSQL structural evidence tests are separate from provider whole-stream integration proof;
- 64 expired admission leases consume zero active slots;
- concurrent lease takeover yields one owner and monotonically increasing epoch;
- stale owner/epoch cannot mutate session/parts, issue capabilities, complete, or abort;
- repository fenced SQL rejects stale upload ownership while direct structural SQL tests do not
  falsely claim to prove caller identity;
- completion work claim uses one runner/epoch; long provider/full-GET work heartbeats independently;
- completion takeover after runner death rejects stale evidence/AVAILABLE/terminal writes;
- upload and completion capacity are separate, and completion does not retain an upload lease;
- terminal sessions cannot mutate;
- direct SQL cannot mutate AVAILABLE Blob/READY Version or invent a mismatching target; and
- no mutable progress counter exists to drift after a crash.

### Pinned MinIO contract

Each test creates isolated keys and aborts/deletes only its own synthetic state:

1. normal multipart upload and full-object verification;
2. interruption after a receipt-backed VERIFIED subset and resume without retransmitting those parts;
3. exact duplicate part replay;
4. different same-number part replacement before completion;
5. two distinct part numbers containing identical bytes expose valid identical ETag/checksum values
   and do not block completion;
6. wrong SHA-256 and signed-length rejection;
7. paginated ListParts reconciliation (fake page size plus live provider smoke);
8. lost UploadPart response with provider part present forces exact re-upload, obtains a new response
   ETag/checksum, and retains reconciled metric attribution;
9. lost Complete response through a faulting transport/proxy;
10. HTTP 200 with an embedded Complete error is surfaced by the adapter and never marks the session
   COMPLETED or Blob AVAILABLE;
11. all-parts-present receipt-backed `COMPLETING` retry remains `COMPLETING` and replays conditional
    Complete using stored response ETags, never ListParts-only values;
12. partial same-MPU `COMPLETING` recovery takes the guarded transition and uploads only unresolved
    parts;
13. Abort followed by `NoSuchUpload`;
14. provider-side `NoSuchUpload` with no final requires a new request/generation/ID with every part unresolved;
15. 409 with absent final requires a new request/generation/ID and uploads every part again;
16. concurrent same-Blob session creation and provider MPU races;
17. unguarded Complete demonstrably overwrites in a probe-only negative test;
18. conditional final completion yields exactly one winner;
19. matching final key is adopted only after full-byte verification; a `FINAL_PRESENT` adoption with
    incomplete session parts never marks those parts resolved, and disappearance before verification
    safely requeues or terminalizes according to provider facts;
20. mismatching final key is never overwritten or deleted;
21. known incomplete loser receives best-effort abort after matching final adoption; abort failure
    does not delete/overwrite final state and remains bounded by provider lifecycle;
22. a part URL used after lease loss may write only exact bytes and is reconciled by the new owner;
23. a missing UploadPart response checksum fails the provider contract, and multipart composite
    checksum is not accepted as whole Blob SHA-256;
24. capability URLs/query canaries, independently exposed provider upload IDs, credentials, and
    absolute paths are absent from logs; and
25. initiation response loss leaves no guessed/adopted provider ID and any known in-memory ID is
    best-effort aborted without claiming success.

### API and CLI

- same push chooses M1 single PUT below/equal threshold and M2 above it;
- create/resolve replay and generation allocation; fenced provider initiation; status, reconcile,
  rolling-window issue, receipt confirm, asynchronous complete, and permitted cancel/abort;
- capability response never exceeds concurrency/hard maximum;
- lease acquisition/renewal/reacquisition uses DB time and a monotonic epoch;
- 64 abandoned invocations cease consuming admission capacity after lease expiry;
- an unexpired competing owner and a fully occupied lease pool return distinct stable retryable
  admission errors;
- stale lease-owner confirm/reconcile/complete/abort is rejected with
  `ADMISSION_LEASE_LOST`/`RETRY_PUSH`;
- `CREATED` cancellation performs no provider call; abort during `INITIATING` returns
  `MULTIPART_INITIATION_IN_PROGRESS`; ambiguous initiation returns the stable ambiguity error and
  requires a new request/generation; an expired owner cannot repeat Create in the same generation;
- complete returns 202, releases the upload lease, and CLI polling/disconnect cannot cancel work;
- two completion runners claim once, heartbeat through a synthetic duration beyond API/upload lease
  limits, fence stale commits, and safely take over after expiry;
- client never advances more than the window and stops scheduling after one failure;
- Ctrl-C leaves the session resumable rather than aborting it;
- resume reports reused, newly transferred, and reconciled parts as disjoint categories;
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

### Deployment and rollback contract

- offline preflight refuses migration while an M1 workflow or v0.1.0 process remains;
- a negative compatibility harness records v0.1.0 with active M2 rows as unsupported and verifies
  deployment orchestration blocks that mixed rollout;
- migration starts only after all v0.1.0 servers are stopped;
- rollback refuses any non-terminal M2 session or unarchived M2 workflow row;
- terminal workflow/part/lease diagnostics can be archived and removed without touching
  DatasetVersion, DatasetEntry, Blob, or final objects; and
- READY Versions and AVAILABLE Blobs remain pull-compatible after permitted downgrade.

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
8. accept asynchronous completion, stop the waiting CLI once, prove the server runner continues,
   then poll through conditional publication, full-byte verification, and Version finalization;
9. pull through the unchanged sequential M1 contract;
10. compare source/restored size and SHA-256 (and `cmp` where practical);
11. scan captured logs for presigned/capability query parameters and credentials; and
12. publish wall times, hashing throughput, push/finalize/pull durations, peak RSS,
    `newly_transferred_part_bytes`, `reused_provider_part_bytes`, `reconciled_part_bytes`,
    `completed_object_bytes`, `verification_read_bytes`, `whole_object_verification_duration`, and
    measurement limitations.

Invocation metrics do not claim wire bytes. If retries partially transmitted bodies, only external
network telemetry may report that traffic.

Failure of checksum, create-only final publication, resume, completion continuity after client
disconnect, byte equality, bounded memory, or secret redaction is a release blocker. Lack of
resources to run the profile is reported, not fabricated.

## Level 3: property and state-machine tests

Use generated Blob sizes and event sequences:

- every accepted plan covers `[0, S)` exactly once, uses `1..N`, and satisfies part limits;
- reconstructing generated expected parts always hashes to the original full digest;
- every illegal session/part transition is rejected by both domain and PostgreSQL;
- replaying immutable request IDs, prepare/reconcile/confirm/complete/abort is idempotent;
- generated terminal attempts never reactivate and new request IDs allocate monotonically increasing
  Blob-scoped generations;
- arbitrary crash points between DB intent, provider call, and DB acknowledgement converge according
  to the failure matrix;
- arbitrary lease expiry/takeover points yield at most one current mutation owner/epoch;
- arbitrary completion-runner crash/heartbeat/takeover points yield at most one committed evidence
  record and terminal transition;
- a stale invocation never performs a successful fenced DB mutation, while late exact provider
  writes remain safely reconcilable;
- provider part sets containing absence, duplication, size mismatch, checksum mismatch, receipt
  absence/mismatch, and page boundaries never produce false progress;
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
