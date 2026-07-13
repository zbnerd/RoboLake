# RoboLake v0.1 Roadmap

## Delivery model

Milestones are ordered vertical slices. Each milestone must leave the repository formatted, typed,
tested, and documented. A milestone is complete only when its acceptance criteria are demonstrated;
later milestones must not be pulled forward as speculative infrastructure.

## M0 — Contracts and local foundation

**Status:** Complete — 2026-07-10

**Goal:** Make the approved v0.1 design runnable as a development skeleton.

**Scope**

- Adopt the modular-monolith package boundaries in [the architecture](ARCHITECTURE_V0_1.md).
- Add Typer, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL driver, S3 client, mypy, and Compose
  configuration using pinned compatible ranges.
- Provide `.env.example`, API/CLI entry points, PostgreSQL and MinIO health checks, and bucket init.
- Establish unit and integration pytest markers and CI commands.

**Acceptance criteria**

- `docker compose up --wait` produces healthy PostgreSQL, MinIO, and API services from a clean clone.
- `robolake --help`, API `/health`, Alembic upgrade, Ruff, mypy, and unit tests succeed; smoke
  integration tests prove a PostgreSQL transaction and MinIO create/head/abort cycle.
- Domain and application packages have automated import-boundary checks prohibiting FastAPI,
  Typer, SQLAlchemy, and S3 SDK imports.
- No dataset-transfer behavior or excluded technology is introduced.

## M1 — Complete create-only vertical slice

**Goal:** Push one regular-file tree as an immutable Version and pull byte-identical files safely.

**Scope**

- Canonical file-only manifest, streamed SHA-256, Linux/macOS path contract, and pre-persistence
  5,000,000,000-byte per-file rejection.
- Dataset/Version/Entry/Blob/UploadSession/idempotency persistence and database invariants.
- Create-only presigned single PUT, provider-attested verification with missing-checksum full-GET
  fallback, whole-file resume, Blob deduplication, status, manifest, and finalization.
- One-capability-at-a-time pull into a private staging tree with atomic no-replace publication.

**Acceptance criteria**

- Required `push`, `status`, `manifest`, and `pull` commands complete the synthetic workflow.
- Same manifest resolves the same Version; changed manifests get registration-order numbers even
  while older Versions are non-READY.
- `200`, `412`, and `409` conditional-write paths converge without overwrite/delete; a stale URL
  cannot mutate an `AVAILABLE` Blob.
- Status separates logical snapshot and unique-content progress without persisted counters; each
  push result separately reports invocation-created/reused Blob outcomes.
- Poisoned objects stop with `CONTACT_OPERATOR`; M1 contains no repair or deletion capability.
- Pull validates every byte/path, issues exactly one bearer capability at a time, and concurrent
  pulls never replace an existing output.
- Baseline filesystem contract tests pass on both Ubuntu and macOS; full PostgreSQL/MinIO evidence
  runs on Linux.
- PostgreSQL/MinIO integration, Ruff, formatting, mypy, pytest, and `scripts/demo-v01.sh` pass.

## M2 — Resumable multipart for large files

**Goal:** Resume inside multi-gigabyte files while retaining M1 identity and create-only semantics.

**Design status:** Under review in PR #5 — 2026-07-13. Implementation has not begun. See the
[M2 product brief](M2_PRODUCT_BRIEF.md), [multipart architecture](M2_MULTIPART_ARCHITECTURE.md), and
[implementation plan](M2_IMPLEMENTATION_PLAN.md).

**Scope**

- Keep the M1 single-PUT path through 5,000,000,000 bytes; add UploadPart persistence only above it.
- Freeze a deterministic 64 MiB-based plan within provider limits and 10,000 parts; issue an exact
  rolling window of short-lived part URLs.
- Separate immutable request replay, CLI invocation metrics, and Blob-scoped provider-attempt
  generations; make provider initiation ambiguity explicit.
- Retain UploadPart response ETags and SHA-256 checksums, use the ETags for Complete, and use
  paginated `ListParts` only to verify current provider state; receipt loss retransmits that exact
  part safely.
- Keep persistent sessions separate from expiring upload admission and completion-runner leases so
  abandoned clients/workers cannot permanently consume their separate caps.
- Accept completion as PostgreSQL work, return 202, then let a same-artifact runner complete directly
  at the final key with `If-None-Match: *` and stream full SHA-256 before `AVAILABLE`.
- Bound abandoned incomplete MPUs with same-workflow abort and provider stale-upload expiry; do not
  add temporary objects, final-object deletion, automatic repair, or general Blob GC.

**Acceptance criteria**

- A synthetic file above the M1 single-PUT limit uploads and pulls byte-identically.
- Killing the CLI after receipt-backed VERIFIED parts sends only unresolved parts on rerun; a part
  whose UploadPart response was lost is safely retransmitted to obtain a receipt.
- Create/resolve replay never rebinds a request; terminal attempts allocate the next generation.
- Completion survives CLI/API disconnect, heartbeats through long verification, and fences stale
  runners after takeover without an external queue.
- Concurrent completion cannot overwrite a completed Blob and converges on one object.
- Lost Complete responses and `NoSuchUpload` converge through the deterministic final key.
- Ambiguous non-409 partial completion resumes only through the guarded same-MPU recovery edge;
  409/`NoSuchUpload` with no final starts a new MPU and retransmits every part.
- Part receipts remain opaque; final integrity uses whole-byte SHA-256 and never treats ETag or a
  multipart composite checksum as Blob identity.
- Normal PR CI uses small multipart fixtures; a scheduled/manual >5,000,000,000-byte profile proves
  bounded memory, resume, final publication, pull, and byte equality.

## M3 — Upload lifecycle cleanup and operator diagnosis

**Goal:** Bound abandoned multipart state and make observed transfer failures diagnosable.

**Scope**

- Reconcile idle sessions, explicitly abort expired multipart uploads, and document provider
  lifecycle cleanup as a second layer.
- Add safe structured correlation and operator runbooks without automatic Blob deletion/repair.

**Acceptance criteria**

- Cleanup never deletes a completed object or mutates an `AVAILABLE` Blob/`READY` Version.
- Crash windows and `NoSuchUpload` converge deterministically under integration tests.
- Logs expose stable resource identifiers/actions but no credentials, absolute paths, dataset bytes,
  or presigned query strings.

## M4 — Threat and platform contract hardening

**Goal:** Prove the supported Linux/macOS and trusted-network contracts under adversarial inputs.

**Scope**

- Extend the baseline Linux/macOS contract across additional kernel/filesystem versions and hostile
  path/race corpora.
- Deepen cursor, capability-expiry, conditional-write, and database-trigger adversarial testing
  beyond the M1 correctness suite.

**Acceptance criteria**

- Security/path corpora and concurrent race tests pass on supported platforms.
- Unsupported atomic publication fails closed; partial outputs are never presented as complete.
- The threat model lists residual risks without claiming Windows, authentication, power-loss
  durability, continuous scrubbing, or automatic repair.

## M5 — v0.1 release evidence

**Goal:** Demonstrate the observed workflow and publish measured operating limits.

**Scope**

- Run the complete failure matrix and synthetic small-/large-file scenarios from a clean clone.
- Finish public CLI/API documentation, upgrade instructions, and operator recovery guidance.

**Acceptance criteria**

- A synthetic dataset with nested paths, duplicate content, many small files, and a file above 5 GB
  survives forced multipart interruption and reconstructs exactly.
- Forced poisoned-object and download-corruption scenarios stop safely with documented action.
- Ruff, formatting, mypy, all unit/integration/security tests, and demos pass in CI.
- README and operator docs state measured limits, supported platforms, and all v0.1 non-goals.

## Beyond v0.1

No next product capability is selected here. After v0.1 is used in the observed workflow, collect
evidence about transfer scale, failures, and the actual downstream consumer before proposing another
scope or architecture decision.
