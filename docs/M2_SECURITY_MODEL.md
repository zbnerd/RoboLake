# RoboLake M2 Security Model

## Scope and trust boundary

M2 retains v0.1.0's trusted-network, single-operator deployment assumption. The API has no
authentication, authorization, or tenant isolation. Anyone who can reach it can create registry
state and obtain narrowly scoped storage capabilities. Request, invocation, session, upload-lease,
completion-lease, and provider-upload identifiers are routing/idempotency/fencing values, not
authorization tokens.

The protected properties are:

- no provider capability, credential, absolute source path, or proprietary byte leaks through
  logs/errors;
- an existing final content-addressed object is never overwritten by a normal workflow;
- only bytes whose whole SHA-256 equals the Blob identity become `AVAILABLE`;
- resumable progress cannot silently substitute different bytes or part boundaries;
- abandoned invocations cannot retain every execution slot forever;
- a timed-out/disconnected client cannot strand accepted completion or let a stale runner publish;
- abandoned incomplete parts are bounded without final-Blob deletion or general GC.

## Threats and controls

| Threat / concrete failure | Selected control | Cost and residual risk | M1 semantic effect |
| --- | --- | --- | --- |
| Unlimited sessions or concurrent-session storm | Blob-locked generation allocation, one active generation per Blob, immutable request binding, expiring upload lease cap, maximum 10,000 parts/session, and request limits. | An unauthenticated caller can still create terminal generations/rows across fake Blobs. Trusted-network placement and external quotas remain mandatory. | None. |
| Request replay aliases another attempt | UUID request record binds canonical digest and session forever; terminal generation never reopens. | Retains idempotency history until an explicit future retention policy. | None. |
| Abandoned sessions permanently consume capacity | Upload capacity counts only unexpired admission leases. Expired leases are atomically reacquired with a higher epoch; persistent session/MPU remains resumable. | Requires heartbeat, DB-time expiry, advisory-lock acquisition, and fencing on every mutation. | None. |
| Stale invocation mutates after takeover | Atomic repository owner/epoch/DB-expiry fence on upload-phase mutations. | An already dispatched provider request cannot be recalled; the current owner reconciles it. DB triggers protect structure, not caller identity. | None. |
| Long Complete/full GET outlives CLI/API timeout | Completion is durable PostgreSQL work claimed by a same-artifact runner with independent renewable lease/heartbeat. | Requires a continuously supervised runner and DB availability; interrupted verification restarts from byte zero. | None. |
| Dead/stale completion runner publishes | Post-provider evidence, AVAILABLE, and terminal writes require current completion owner/epoch/expiry. Takeover reconciles deterministic final key first. | Two provider calls may overlap after lease expiry, but conditional final publication and fencing converge. | None. |
| Lost UploadPart response leads to fabricated ETag | Store UploadPart response receipts; ListParts only verifies current state. Receipt-less listed parts are re-uploaded. | Safe retransmission costs bandwidth. AWS portability is documentation-derived until live AWS contract tests. | None. |
| Ambiguous provider initiation leaves unknown MPU | Explicit `INITIATING`; ambiguity terminates generation, never guesses/adopts ID; best-effort abort only when in-memory ID remains; lifecycle cleanup is backstop. | Unknown MPU may consume storage until lifecycle policy. | None. |
| Tiny-part abuse | Server computes the frozen 64 MiB-based algorithm; clients cannot select boundaries. | Maximum-size objects still create up to 10,000 rows/requests. | None. |
| Oversized object or metadata | 5,000,000,000,000-byte M2 Blob maximum, 10,000 parts, existing 100,000-entry/64 MiB manifest limits, strict integer checks. | Operators must provision storage quotas externally. | Adds M2-only transfer limit. |
| Presigned capability leakage | 900-second TTL, rolling window <= concurrency, exact key/upload/part/length/checksum signing, query redaction. | A bearer can replay the exact part until expiry even after lease loss; it cannot change digest/size or call API mutation with a stale fence. | Extends M1 capability rule. |
| Part-number substitution | Part number and provider upload ID are signed; API issues only frozen plan members under a current lease. | Provider error text is never trusted/exposed. | None. |
| Checksum omission or tampering | `Content-Length` and `x-amz-checksum-sha256` are signed; confirmation stores response receipt and verifies through ListParts. | Unsupported providers fail closed. | None. |
| Replay after part verification | Replayed URL can replace only the same part number with the same length/SHA-256 while the MPU exists. | Consumes bandwidth and may change opaque ETag; API reconciles again. | None. |
| Source file changes during resume | Full and per-part hashes before transfer, positional-read rehash, final stable whole-file rehash before Complete. | Changes can waste transfer but cannot publish a different Blob identity. | Strengthens M1 stability without redefining identity. |
| Temporary-key collision/leakage | Selected architecture has no temporary key. | No temp cleanup namespace exists. | None. |
| Concurrent final publication | `CompleteMultipartUpload(If-None-Match: *)`, final-key reconciliation, and one DB active session. | AWS 409 invalidates the provider attempt and requires full restart. Losing incomplete MPUs consume storage until abort/lifecycle. | Preserves create-only Blob rule. |
| Malformed part plan targets a real SHA key | Server freezes plan and signed parts; final object receives whole-stream SHA-256 before AVAILABLE. | Wrong bytes can occupy a non-AVAILABLE final key and require operator intervention; accepted inside trusted M2 boundary. | Uses ADR 0007 detect/report/stop. |
| Database row exhaustion | Complete but bounded frozen plan, 10,000-row/session cap, and bounded active leases under advisory lock. | Persistent terminal/session history still grows; retention is not invented in M2. | None. |
| Incomplete-upload exhaustion | Explicit same-workflow abort and Compose stale cleanup every 6 h with 168 h expiry. | Unknown Create-response-loss MPU can consume storage until provider cleanup; timed expiry was not empirically observed. | Adds only incomplete-MPU cleanup. |
| Poisoned final key | Full mismatch returns `STORED_OBJECT_MISMATCH`, exit 5, `CONTACT_OPERATOR`; no overwrite/delete/repair API. | Availability can remain blocked pending independent operator inspection. | Exactly preserves ADR 0007. |
| Provider upload ID disclosure | Never expose as a stable API field; redact full capability URL/query and provider bodies. | It assists storage operations if combined with credentials but is not authorization alone. | None. |
| Mixed v0.1.0/M2 deployment | Offline rollout drains/stops every v0.1 process before migration and M2 admission. | First M2 release has downtime; rolling compatibility is deferred. | No M1 protocol change. |

## Resource and lease limits

Recommended operational defaults:

```text
MAX_ACTIVE_MULTIPART_LEASES = 64
MULTIPART_ADMISSION_LEASE_TTL_SECONDS = 120
MULTIPART_ADMISSION_RENEW_INTERVAL_SECONDS = 30
MULTIPART_UPLOAD_CONCURRENCY = 4
MAX_MULTIPART_UPLOAD_CONCURRENCY = 16
MULTIPART_COMPLETION_CONCURRENCY = 2
MAX_MULTIPART_COMPLETION_CONCURRENCY = 8
MULTIPART_COMPLETION_LEASE_TTL_SECONDS = 120
MULTIPART_COMPLETION_HEARTBEAT_SECONDS = 30
PRESIGNED_URL_TTL_SECONDS = 900
STREAM_CHUNK_BYTES = 1_048_576
```

Upload/completion lease TTLs, heartbeat intervals, both concurrency caps, active upload-lease
capacity, and capability TTL may vary by deployment without changing a stored session or plan.
Startup validation requires positive values, heartbeat at most one third of its lease TTL, upload
concurrency `1..16`, completion concurrency `1..8`, and at least one slot. Multiple processes use
PostgreSQL transaction time and atomic acquisition/claim transactions.

If 64 invocations disappear, their persistent sessions and provider MPUs remain. After 120 seconds
without renewal the leases no longer count against capacity; a new invocation can acquire a slot.
Taking over one session atomically increments its epoch. No cleanup, provider abort, or state expiry
is inferred from lease expiry.

Completion rows do not consume upload capacity. Only unexpired completion leases count against the
separate completion cap. A runner heartbeats every 30 seconds in independent short transactions
while provider Complete or full-byte verification is in flight. Expiry permits takeover but does not
change `COMPLETING`; only the current epoch may commit observations or terminal state.

## Capability and fencing handling

An acquire request carries UUID `request_id` and `invocation_id`; the server generates the non-secret
upload lease owner UUID/epoch. Mutating API requests carry that owner and epoch. One atomic repository
predicate rejects a missing, expired, or stale fence with `ADMISSION_LEASE_LOST`/`RETRY_PUSH`. The CLI
renews while part requests are active. The API only accepts completion work and returns 202; the CLI
never receives a provider completion capability, so a stale CLI cannot publish a final object.

A part URL issued before lease loss may remain usable. It can write only the signed checksum/length
to the same part number. The current owner classifies any later matching discovery as reconciled;
the stale owner cannot confirm it or modify state. If an API provider control request was already in
flight at lease loss, its DB result is fenced and the current owner performs deterministic
reconciliation. Completion runner owner/epoch values stay server-side. A stale runner cannot write
verification evidence or AVAILABLE after completion-lease takeover.

Structured logs may contain safe session UUID, Blob digest prefix, part number, lease epoch, error
code, and correlation ID. They must not contain request/response bodies with URLs, HTTP debug
traces, `Authorization`, query strings, provider bodies, standalone provider upload IDs, or local
absolute paths. `PresignedRequest` remains `repr=False`; error translation constructs safe output.

Reverse-proxy logs must omit request bodies and redact object-storage query strings. Tests inject a
canary token and assert absence from captured logs/errors.

## Cleanup boundary

M2 may abort only an incomplete provider upload whose ID is durably recorded for the current session:
the current upload lease owns an explicit abort, while the current completion lease may best-effort
abort a known losing MPU after matching final adoption. `CREATED` cancels locally; `INITIATING`
cannot enter `ABORTING`.
Ctrl-C does not abort because interruption is the resume use case. Explicit abort waits for in-flight
workers, records `ABORTING`, requests provider abort, and proves absence. Provider stale cleanup is
the backstop for unknown initiation outcomes and abandoned MPUs.

M2 adds no periodic application sweeper, temporary-object deletion, final-object deletion, repair,
or Blob GC. Admission-lease expiry is capacity reclamation, not data cleanup. The only approved
manual exception for a poisoned non-AVAILABLE key is the evidence-preserving
[operator runbook](M2_POISONED_FINAL_KEY_RUNBOOK.md).

## Residual risks

- No authentication means network placement is the principal access control.
- A malicious/buggy trusted caller can consume persistent DB/provider capacity or poison a
  non-AVAILABLE key, though it cannot make wrong bytes AVAILABLE or overwrite an existing key.
- Provider lifecycle timing and capacity remain operational dependencies; initiation ambiguity may
  leave an unknown MPU until lifecycle cleanup.
- The completion runner needs supervision and database availability; M2 has no external queue or
  live byte-level verification checkpoint.
- AWS receipt/completion portability follows official documentation but has not yet been live-tested
  against AWS.
- Object storage can corrupt after READY; pull detects it, but M2 adds no continuous auditor.
- SHA-256 collision resistance and provider conditional-write/checksum behavior are trusted.
- Database and object-store loss require coordinated backup; neither alone reconstructs the registry.
