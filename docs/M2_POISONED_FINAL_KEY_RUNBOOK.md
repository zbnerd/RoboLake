# M2 Poisoned Final-Key Operator Runbook

## Purpose and boundary

Use this procedure only when the deterministic final object exists but whole-object verification did
not allow the Blob to become `AVAILABLE`. Typical causes are a malformed trusted-client part plan,
provider defect, or a completion outcome whose assembled bytes do not match the SHA-256 encoded in
the key.

This is a manual incident procedure, not an M2 repair API, background job, or Blob garbage collector.
The normal application credential must not have final-object delete permission. If any precondition
below cannot be proven, stop and preserve the object for further investigation.

## Safety invariants

- Never overwrite or delete an `AVAILABLE` Blob.
- Never delete an object referenced by a `READY` DatasetVersion.
- Never infer integrity from ETag, caller metadata, multipart composite checksum, or HTTP status.
- Never use a wildcard/prefix deletion; operate on one exact deterministic key.
- Stop API/CLI writes before deletion so DB/provider state cannot race the inspection.
- Preserve evidence before changing provider state.

## 1. Open an incident and quiesce writes

Record an incident/correlation identifier, UTC start time, operator identity, reason, affected Blob
SHA-256, Dataset/Version references, and the approval authorizing storage intervention. Stop the
RoboLake API or otherwise prove no push/complete/abort request can run. Pull may remain unavailable
for this non-AVAILABLE Blob.

Do not put credentials, presigned URLs, query strings, absolute source paths, or dataset bytes in the
incident log. Use a separately configured least-privilege storage-operator alias; do not grant
DeleteObject to the RoboLake application principal.

## 2. Derive the exact key

Validate the expected digest as 64 lowercase hexadecimal characters, then derive—not copy from an
error message—the only permitted key:

```bash
export BLOB_SHA='<64-lowercase-hex-sha256>'
test "$(printf '%s' "$BLOB_SHA" | LC_ALL=C tr -cd '0-9a-f' | wc -c)" -eq 64
test "${#BLOB_SHA}" -eq 64
export OBJECT_KEY="blobs/sha256/${BLOB_SHA:0:2}/${BLOB_SHA:2:2}/${BLOB_SHA}"
```

The storage bucket comes from approved deployment configuration, for example
`ROBOLAKE_S3_BUCKET`; do not paste credentials into the command line.

## 3. Inspect PostgreSQL read-only state

Run these queries in a read-only transaction, binding `:blob_sha` through the SQL client rather than
string concatenation:

```sql
BEGIN TRANSACTION READ ONLY;

SELECT id, sha256, size_bytes, object_key, state, failure_code,
       failure_detail, created_at, verified_at
FROM blobs
WHERE sha256 = :blob_sha;

SELECT d.name, dv.id AS version_id, dv.version_number, dv.state AS version_state,
       de.relative_path
FROM blobs b
JOIN dataset_entries de ON de.blob_id = b.id
JOIN dataset_versions dv ON dv.id = de.dataset_version_id
JOIN datasets d ON d.id = dv.dataset_id
WHERE b.sha256 = :blob_sha
ORDER BY d.name, dv.version_number, de.relative_path;

SELECT us.id, us.strategy, us.state, us.failure_code,
       us.created_at, us.last_activity_at, us.completed_at
FROM blobs b
JOIN upload_sessions us ON us.blob_id = b.id
WHERE b.sha256 = :blob_sha
ORDER BY us.created_at;

SELECT count(*) AS ready_reference_count
FROM blobs b
JOIN dataset_entries de ON de.blob_id = b.id
JOIN dataset_versions dv ON dv.id = de.dataset_version_id
WHERE b.sha256 = :blob_sha AND dv.state = 'READY';

ROLLBACK;
```

Stop immediately if the Blob row is absent, `state = 'AVAILABLE'`, `ready_reference_count <> 0`,
`object_key` differs from the derived key, or a session is active in `CREATED`, `IN_PROGRESS`,
`COMPLETING`, or `ABORTING`. Do not edit these rows manually. Resolve/abort an active session through
the approved application workflow, then restart this inspection from step 1.

## 4. Inspect and stream-verify provider bytes

Capture provider-owned size, ETag, checksum fields/type, version ID if enabled, last-modified time,
and request/correlation IDs without recording a signed URL:

```bash
mkdir -p incident-evidence
mc stat --json "operator/${ROBOLAKE_S3_BUCKET}/${OBJECT_KEY}" \
  > incident-evidence/object-stat.json
```

Stream every byte and record the actual SHA-256. Linux:

```bash
mc cat "operator/${ROBOLAKE_S3_BUCKET}/${OBJECT_KEY}" \
  | sha256sum \
  | tee incident-evidence/whole-object-sha256.txt
```

macOS:

```bash
mc cat "operator/${ROBOLAKE_S3_BUCKET}/${OBJECT_KEY}" \
  | shasum -a 256 \
  | tee incident-evidence/whole-object-sha256.txt
```

Also compare provider size with `blobs.size_bytes`. A partial read, network error, or verifier error
is inconclusive; repeat from the beginning. Do not adopt or delete based on a partial digest.

## 5A. Matching object: adopt through normal reconciliation

When exact size and whole-object SHA-256 both match the Blob row/key, do not delete, copy, overwrite,
or update SQL manually. Preserve the evidence, restore API service, and rerun the original idempotent
push with the same Dataset and source manifest:

```bash
uv run robolake push '<validated-source-root>' --dataset '<dataset-name>'
```

The source must independently rescan to the same manifest. The M2 complete/reconciliation workflow
performs its own full provider read and may then adopt the object and mark the Blob `AVAILABLE`.
Verify session state, Blob state, and Version finalization through normal status/API responses. If
the exact source is unavailable or reconciliation still fails, stop; manual SQL adoption is not an
approved M2 operation.

## 5B. Mismatching object: deletion preconditions

An operator may delete the exact object only when all of the following are freshly proven and
recorded:

1. API/CLI writes are quiesced.
2. The DB Blob exists and is not `AVAILABLE`.
3. No `READY` DatasetVersion references the Blob.
4. No active UploadSession/MPU exists for the Blob.
5. DB `object_key` equals the key derived from DB SHA-256.
6. Provider object exists at that exact key and provider size and/or a complete streamed SHA-256
   differs from immutable Blob identity.
7. Object stat, DB query output, actual digest, timestamps, correlation/request IDs, and operator
   approval are preserved in restricted incident evidence.
8. A storage operator using credentials separate from RoboLake application credentials has approved
   deletion of that single non-AVAILABLE object.

If policy requires retaining the mismatching bytes, export them to approved restricted incident
storage before deletion and record its independent digest. Do not copy them into another RoboLake
Blob key or treat the evidence copy as registry content.

Delete exactly one key—never a prefix:

```bash
mc rm --force "operator/${ROBOLAKE_S3_BUCKET}/${OBJECT_KEY}"
mc stat "operator/${ROBOLAKE_S3_BUCKET}/${OBJECT_KEY}"
```

The final `mc stat` must report absence. An unexpected success means the object still exists; stop
before restarting writes.

## 6. Recover and close

Restore the API, rerun the same `robolake push`, and verify:

- the same immutable DatasetVersion/manifest is resolved;
- a new MPU is used where required;
- the final object passes whole-object SHA-256;
- Blob reaches `AVAILABLE` only through the normal verification transaction;
- Version reaches `READY` only after every referenced Blob is AVAILABLE; and
- no presigned capability or credential entered the incident record.

Record the final Blob/session/Version states, verification duration/bytes, command exit statuses,
and incident disposition. Keep the evidence according to operator retention policy.

## Forbidden shortcuts

- Do not update Blob/session/Version state directly with SQL.
- Do not delete an AVAILABLE object even if a later independent read appears corrupt.
- Do not overwrite the final key with a local file, temporary object, or server-side copy.
- Do not reuse a 409-conflicted provider upload ID.
- Do not automate this runbook in M2.

## Related decisions

- [ADR 0007: manual repair boundary](adr/0007-failed-publication-and-manual-repair.md)
- [ADR 0009: multipart final publication](adr/0009-multipart-final-publication.md)
- [M2 security model](M2_SECURITY_MODEL.md)
- [M2 failure matrix](M2_FAILURE_AND_RECONCILIATION_MATRIX.md)
