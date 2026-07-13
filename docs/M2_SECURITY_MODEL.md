# RoboLake M2 Security Model

## Scope and trust boundary

M2 retains v0.1.0's trusted-network, single-operator deployment assumption. The API has no
authentication, authorization, or tenant isolation. Anyone who can reach it can create registry
state and obtain narrowly scoped storage capabilities. Multipart session UUIDs, admission owner
UUIDs/epochs, and provider upload IDs are routing/fencing values, not authorization tokens.

The protected properties are:

- no provider capability, credential, absolute source path, or proprietary byte leaks through
  logs/errors;
- an existing final content-addressed object is never overwritten by a normal workflow;
- only bytes whose whole SHA-256 equals the Blob identity become `AVAILABLE`;
- resumable progress cannot silently substitute different bytes or part boundaries;
- abandoned invocations cannot retain every execution slot forever; and
- abandoned incomplete parts are bounded without final-Blob deletion or general GC.

## Threats and controls

| Threat / concrete failure | Selected control | Cost and residual risk | M1 semantic effect |
| --- | --- | --- | --- |
| Unlimited sessions or concurrent-session storm | One persistent active session per Blob; a separate expiring admission lease with global cap; maximum 10,000 parts/session; manifest/request limits. | A reachable unauthenticated caller can still churn leases and create persistent rows across fake Blobs. Trusted-network placement and external storage/DB quotas remain mandatory. | None. |
| Abandoned sessions permanently consume capacity | Global capacity counts only unexpired leases. Expired leases are atomically reacquired with a higher epoch; persistent session/MPU remains resumable. | Requires lease heartbeat, DB-time expiry, advisory-lock acquisition, and fencing on every mutation. | None. |
| Stale invocation mutates after takeover | Owner/epoch/expiry fence on capability issuance, reconcile writes, confirm, Complete initiation, abort, and post-provider DB commits. | An already dispatched provider request cannot be recalled; the current owner must reconcile it. | None. |
| Tiny-part abuse | Server computes the frozen 64 MiB-based algorithm; clients cannot select boundaries. | Maximum-size objects still create up to 10,000 rows/requests. | None. |
| Oversized object or metadata | 50,000,000,000,000-byte M2 Blob maximum, 10,000 parts, existing 100,000-entry/64 MiB manifest limits, strict integer checks. | Operators must provision storage quotas externally. | Adds M2-only transfer limit. |
| Presigned capability leakage | 900-second TTL, rolling window <= concurrency, exact key/upload/part/length/checksum signing, query redaction. | A bearer can replay the exact part until expiry even after lease loss; it cannot change digest/size or call API mutation with a stale fence. | Extends M1 capability rule. |
| Part-number substitution | Part number and provider upload ID are signed; API issues only frozen plan members under a current lease. | Provider error text is never trusted/exposed. | None. |
| Checksum omission or tampering | `Content-Length` and `x-amz-checksum-sha256` are signed; confirmation uses ListParts. | Unsupported providers fail closed. | None. |
| Replay after part verification | Replayed URL can replace only the same part number with the same length/SHA-256 while the MPU exists. | Consumes bandwidth and may change opaque ETag; API reconciles again. | None. |
| Source file changes during resume | Full and per-part hashes before transfer, positional-read rehash, final stable whole-file rehash before Complete. | Changes can waste transfer but cannot publish a different Blob identity. | Strengthens M1 stability without redefining identity. |
| Temporary-key collision/leakage | Selected architecture has no temporary key. | No temp cleanup namespace exists. | None. |
| Concurrent final publication | `CompleteMultipartUpload(If-None-Match: *)`, final-key reconciliation, and one DB active session. | AWS 409 invalidates the provider attempt and requires full restart. Losing incomplete MPUs consume storage until abort/lifecycle. | Preserves create-only Blob rule. |
| Malformed part plan targets a real SHA key | Server freezes plan and signed parts; final object receives whole-stream SHA-256 before AVAILABLE. | Wrong bytes can occupy a non-AVAILABLE final key and require operator intervention; accepted inside trusted M2 boundary. | Uses ADR 0007 detect/report/stop. |
| Database row exhaustion | Lazy part rows, 10,000-row/session cap, bounded active leases under advisory lock. | Persistent terminal/session history still grows; retention is not invented in M2. | None. |
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
PRESIGNED_URL_TTL_SECONDS = 900
STREAM_CHUNK_BYTES = 1_048_576
```

Lease TTL, renew interval, concurrency, active-lease capacity, and capability TTL may vary by
deployment without changing a stored session or part plan. Startup validation requires positive
values, renew interval below lease TTL, concurrency `1..16`, and at least one lease slot. Multiple
API instances use PostgreSQL transaction time and an advisory-lock acquisition transaction.

If 64 invocations disappear, their persistent sessions and provider MPUs remain. After 120 seconds
without renewal the leases no longer count against capacity; a new invocation can acquire a slot.
Taking over one session atomically increments its epoch. No cleanup, provider abort, or state expiry
is inferred from lease expiry.

## Capability and fencing handling

Mutating API requests carry the non-secret lease owner UUID and epoch. The repository rejects a
missing, expired, or stale fence with `ADMISSION_LEASE_LOST`/`RETRY_PUSH`. The CLI renews while part
requests are active. Complete remains API-owned: the CLI never receives a provider completion
capability, so a stale CLI cannot publish a final object directly.

A part URL issued before lease loss may remain usable. It can write only the signed checksum/length
to the same part number. The current owner classifies any later matching discovery as reconciled;
the stale owner cannot confirm it or modify state. If an API provider control request was already in
flight at lease loss, its DB result is fenced and the current owner performs deterministic
reconciliation.

Structured logs may contain safe session UUID, Blob digest prefix, part number, lease epoch, error
code, and correlation ID. They must not contain request/response bodies with URLs, HTTP debug
traces, `Authorization`, query strings, provider bodies, standalone provider upload IDs, or local
absolute paths. `PresignedRequest` remains `repr=False`; error translation constructs safe output.

Reverse-proxy logs must omit request bodies and redact object-storage query strings. Tests inject a
canary token and assert absence from captured logs/errors.

## Cleanup boundary

M2 may abort only an incomplete provider upload belonging to the current session and current lease
owner. Ctrl-C does not abort because interruption is the resume use case. Explicit abort waits for
in-flight workers, records `ABORTING`, requests provider abort, and then proves absence. Provider
stale cleanup remains the backstop for unknown or abandoned MPUs.

M2 adds no periodic application sweeper, temporary-object deletion, final-object deletion, repair,
or Blob GC. Admission-lease expiry is capacity reclamation, not data cleanup. The only approved
manual exception for a poisoned non-AVAILABLE key is the evidence-preserving
[operator runbook](M2_POISONED_FINAL_KEY_RUNBOOK.md).

## Residual risks

- No authentication means network placement is the principal access control.
- A malicious/buggy trusted caller can consume persistent DB/provider capacity or poison a
  non-AVAILABLE key, though it cannot make wrong bytes AVAILABLE or overwrite an existing key.
- Provider lifecycle timing and capacity remain operational dependencies.
- Object storage can corrupt after READY; pull detects it, but M2 adds no continuous auditor.
- SHA-256 collision resistance and provider conditional-write/checksum behavior are trusted.
- Database and object-store loss require coordinated backup; neither alone reconstructs the registry.
