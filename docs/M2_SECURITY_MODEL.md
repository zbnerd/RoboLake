# RoboLake M2 Security Model

## Scope and trust boundary

M2 retains v0.1.0's trusted-network, single-operator deployment assumption. The API has no
authentication, authorization, or tenant isolation. Anyone who can reach it can create registry
state and obtain narrowly scoped storage capabilities. Multipart session UUIDs and provider upload
IDs are not authorization tokens.

The protected properties are:

- no provider capability, credential, absolute source path, or proprietary byte leaks through
  logs/errors;
- an existing final content-addressed object is never overwritten by a normal workflow;
- only bytes whose whole SHA-256 equals the Blob identity become `AVAILABLE`;
- resumable progress cannot silently substitute different bytes or part boundaries; and
- abandoned incomplete parts are bounded without introducing final-Blob deletion or general GC.

## Threats and controls

| Threat / concrete failure | Selected control | Cost and residual risk | M1 semantic effect |
| --- | --- | --- | --- |
| Unlimited sessions or concurrent-session storm | One active DB session per Blob, transactional global active-session operational cap, maximum 10,000 parts/session, manifest limits, bounded request body. | A reachable unauthenticated caller can still consume the deployment-wide quota across many fake Blobs. Trusted-network placement remains mandatory. | None. |
| Tiny-part abuse | Server computes the frozen 64 MiB-based algorithm; clients cannot select boundaries. | Large maximum-size objects still create up to 10,000 rows/requests. | None. |
| Oversized object or metadata | 50,000,000,000,000-byte M2 Blob maximum, 10,000 parts, existing 100,000-entry/64 MiB manifest limits, strict integer overflow checks. | M2 intentionally supports very large storage consumption; operators must provision quotas externally. | Adds M2-only transfer limit. |
| Presigned capability leakage | 900-second TTL, rolling window <= concurrency, exact key/upload/part/length/checksum signing, query-string redaction in client/API/proxy logs and exceptions. | A bearer can replay the same exact part until expiry; it cannot change digest/size. | Extends existing M1 capability rule. |
| Part-number substitution | Part number and provider upload ID are signed into the URL; API issues only frozen plan members. | Provider error text is never trusted or exposed. | None. |
| Checksum omission or tampering | `Content-Length` and `x-amz-checksum-sha256` are signed; confirmation uses provider ListParts, not caller receipt. | Providers must pass the pinned contract; unsupported providers fail closed. | None. |
| Replay after part verification | Replayed URL can replace only the same part number with the same length/SHA-256. | Consumes bandwidth and changes opaque ETag, so API reconciles again; data identity remains fixed. | None. |
| Source file changes during resume | Full and per-part hashes from a stable pass before any resumed transfer; positional-read part rehash; final stable whole-file rehash before Complete. | Changes can waste transfer before the final check, but cannot publish a different Blob identity. | Strengthens, does not redefine, M1 source stability. |
| Temporary-key collision/leakage | Selected architecture has no temporary key. | No temp cleanup namespace exists. | None. |
| Concurrent final publication | `CompleteMultipartUpload(If-None-Match: *)`, final-key reconciliation, and one DB active session. | AWS can return 409; the attempt must restart. Losing incomplete MPUs consume storage until abort/lifecycle. | Preserves create-only Blob rule. |
| Malformed client part plan targets a real SHA key | Server checks deterministic shape and signed parts; after completion it streams the whole object and refuses `AVAILABLE` unless full SHA matches. | Because providers cannot validate whole SHA-256 at multipart completion, bad bytes may occupy a **non-AVAILABLE** final key before detection, causing operator intervention. This is accepted only inside the trusted M2 boundary; temp/copy would reduce this client risk but adds major I/O/lifecycle and lacked pinned MinIO checksum support. | Uses the existing poisoned-key detect/report/stop rule; never silently publishes. |
| Database row exhaustion | Lazy part rows only when a large Blob session starts; hard 10,000 rows/session; operational global active-session cap acquired under PostgreSQL advisory lock. | Completed history still grows; retention is not invented in M2. | None. |
| MinIO incomplete-upload exhaustion | Explicit abort for the same workflow, reconciliation after abort, Compose stale cleanup every 6 h with 168 h expiry. | Create-response-loss can leave an unknown MPU until provider cleanup; timed expiry was not empirically accelerated. | Adds only incomplete-MPU cleanup. |
| Poisoned final key | Full-byte mismatch returns `STORED_OBJECT_MISMATCH`, CLI exit 5, `CONTACT_OPERATOR`; no overwrite/delete/repair API. | Availability can remain blocked until an operator independently inspects storage. | Exactly preserves ADR 0007. |
| Provider upload ID disclosure | Do not expose it as an independent stable API field. A presigned UploadPart URL necessarily contains it and `partNumber`; redact the entire URL/query and forbid client interpretation. | It is not authorization by itself but assists storage operations if combined with credentials. | None. |

## Resource limits

Recommended operational defaults:

```text
MAX_ACTIVE_MULTIPART_SESSIONS = 64
MULTIPART_UPLOAD_CONCURRENCY = 4
MAX_MULTIPART_UPLOAD_CONCURRENCY = 16
PRESIGNED_URL_TTL_SECONDS = 900
STREAM_CHUNK_BYTES = 1_048_576
```

Concurrency and active-session capacity may vary by deployment without changing a stored session.
Part size/count/object maximum are protocol constants. Startup validation requires concurrency
`1..16`, positive TTL/chunk size, and an active-session limit no lower than one. Multiple API
instances enforce the global active cap inside PostgreSQL rather than process memory.

## Capability handling

The API/CLI must use structured logging with fields such as safe session UUID, Blob digest prefix,
part number, error code, and correlation ID. It must not log request/response bodies containing URLs,
HTTP client debug traces, `Authorization`, query strings, provider bodies, independently rendered
provider upload IDs, or local absolute paths. The `PresignedRequest` remains `repr=False`; error
translation constructs a new safe message rather than interpolating an exception.

Reverse-proxy access logs must omit request bodies and redact query strings on object-storage
requests. Tests inject a canary query token and assert it is absent from captured logs/errors.

## Cleanup boundary

M2 may abort only an incomplete provider upload belonging to the current session. Ctrl-C does not
abort because interruption is the resume use case. Explicit abort waits for in-flight workers,
requests provider abort, then proves absence. Provider stale cleanup is a second layer for unknown
or abandoned MPUs.

M2 adds no periodic application sweeper, temporary-object deletion, final-object deletion, repair,
or Blob GC. A general maintenance command and auditable operator workflow remain later decisions.
The only approved manual exception for a poisoned non-AVAILABLE final key is the evidence-preserving
operator procedure in [the poisoned final-key runbook](M2_POISONED_FINAL_KEY_RUNBOOK.md); it is not
an application repair feature.

## Residual risks

- No authentication means network placement is the principal access control.
- A malicious/buggy trusted caller can create a mismatching non-AVAILABLE final object and force
  operator intervention, though it cannot make wrong bytes AVAILABLE or overwrite an existing key.
- Provider lifecycle timing and capacity are operational dependencies.
- Object storage can corrupt after READY; pull detects it, but M2 adds no continuous auditor.
- SHA-256 collision resistance and correct provider conditional-write/checksum behavior are trusted.
- Database loss and object-store loss require coordinated backup; neither alone reconstructs the
  complete registry.
