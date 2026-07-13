# M2 Provider Probes

## Environment and method

Probes ran on 2026-07-13 against the exact Compose image, in a fresh randomly named bucket using
synthetic deterministic bytes:

```text
image: minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e
server: RELEASE.2025-09-07T16-13-09Z, commit 07c3a429bfed433e49018cb0f78a52145d4bedeb
runtime: go1.24.6 linux/amd64
boto3: 1.43.45
botocore: 1.43.45
httpx: 0.28.1
endpoint: local Compose MinIO on Linux/amd64
```

Commands:

```bash
docker compose up -d --wait
docker inspect robolake-minio-1 --format '{{.Config.Image}} {{.Image}}'
docker exec robolake-minio-1 minio --version
set -a
. ./.env.example
set +a
uv run python /tmp/robolake_m2_probe.py
uv run python /tmp/robolake_m2_copy_probe.py
```

The temporary harnesses used the exact SDK calls shown below, printed structured results without
URLs or credentials, aborted every remaining MPU, deleted synthetic objects/buckets, and were then
removed. M2 implementation must promote these calls into pinned integration contract tests rather
than rely on the prose report.

Common initiation and part calls were:

```python
s3.create_multipart_upload(Bucket=bucket, Key=key, ChecksumAlgorithm="SHA256")
s3.upload_part(
    Bucket=bucket,
    Key=key,
    UploadId=upload_id,
    PartNumber=number,
    ContentLength=len(data),
    ChecksumSHA256=base64_sha256,
    Body=data,
)
s3.list_parts(Bucket=bucket, Key=key, UploadId=upload_id)
s3.complete_multipart_upload(
    Bucket=bucket,
    Key=key,
    UploadId=upload_id,
    MultipartUpload={"Parts": provider_receipts},
    IfNoneMatch="*",
)
```

The presigned UploadPart was generated with the same `ContentLength` and `ChecksumSHA256` params and
sent by `httpx.put` with those exact headers. No presigned URL or query string was printed.

## Mandatory evidence

| # | Probe and exact operation | Observed result and object state | Portability assessment |
| ---: | --- | --- | --- |
| 1 | `create_multipart_upload(..., ChecksumAlgorithm="SHA256")` | 200 with opaque UploadId; no object visible at key. | Standard S3 behavior. |
| 2 | Presign and execute `UploadPart` with signed length/SHA-256 | 200. Signed headers were exactly `content-length`, `host`, `x-amz-checksum-sha256`. Changing signed length/checksum returned 403. | AWS SigV4/UploadPart supports the same fields. Contract-test every provider. |
| 3 | `list_parts` after part 1 | Returned number 1, exact 1,048,576-byte size, ETag, and matching `ChecksumSHA256`. | Standard API, but page size differs: AWS returns at most 1,000; implementation must paginate. |
| 4 | Upload identical bytes again at part number 1 | Same ETag/checksum and one listed part. | AWS documents same-number replacement. Exact replay is safe. |
| 5 | Upload different bytes at part number 1 | Listed part was replaced; ETag and SHA-256 changed; count stayed one. | AWS documents replacement. This is why every capability must bind expected digest/length. |
| 6 | Upload bytes with the wrong `ChecksumSHA256` | HTTP 400 `XAmzContentChecksumMismatch`; no valid part adopted. | Error code is MinIO-specific; rejection semantics are portable. CLI must not parse body/code. |
| 7 | Complete 6 MiB + 12,345 B parts, conditional | 200; final size 6,303,801 B; bytes matched. HEAD exposed `ChecksumType=COMPOSITE`, SHA-256 value ending `-2`, and multipart ETag ending `-2`. | AWS SHA-256 multipart checksum is also composite. Neither composite checksum nor ETag is full Blob identity. |
| 8 | Complete successfully, deliberately discard SDK response, then reconcile | HEAD found the final object; full GET SHA-256 matched. | This simulates loss at the application response boundary, not a packet-level TCP fault. Deterministic-key reconciliation is provider-neutral. |
| 9 | Repeat Complete with same ID/parts | With `If-None-Match:*`, MinIO returned 412 because final exists. Without the condition it returned 404 `NoSuchUpload`. Final bytes remained unchanged. | Exact replay code may vary; clients must inspect final key before interpreting it. |
| 10 | `abort_multipart_upload` after one part | HTTP 204. | Standard. AWS warns in-flight parts can finish, so abort must be followed by reconciliation. |
| 11 | `list_parts` after abort | 404 `NoSuchUpload`; parts no longer listed. | Standard observable outcome. |
| 12 | Abort as simulated provider-side disappearance, then status/complete | List returned `NoSuchUpload`; Complete with an empty invalid list returned 400 `InvalidRequest`. No final object appeared. | Do not key logic to the Complete error. HEAD then ListParts is the portable decision order. |
| 13 | `list_parts` with a random upload ID | 404 `NoSuchUpload`. | Standard name; transport/code must still be adapter-translated. |
| 14 | Initiate two MPUs at one final key | Both existed concurrently and each accepted its own part. | AWS explicitly permits concurrent uploads to one key. PostgreSQL uniqueness reduces but cannot replace final conditions. |
| 15 | Complete both same-key MPUs without a condition | First bytes appeared, then second completion overwrote them. A separate existing object was likewise overwritten. | Portable danger. Unconditional Complete is forbidden. |
| 16 | Complete a temporary object, then `CopyObject` to final | First copy returned 200 and bytes matched. | Single CopyObject cannot publish M2-scale objects on AWS; files above 5 GB need multipart copy. |
| 17 | Repeat `CopyObject(..., IfNoneMatch="*")` from different source | A botocore `before-send.s3.CopyObject` hook confirmed the actual request carried `If-None-Match: *`. MinIO returned 200 and overwrote the existing final bytes: the destination condition was ignored. | **MinIO incompatibility.** Current AWS docs describe destination conditional CopyObject, but this pinned MinIO image does not enforce it. Option B cannot use simple copy. |
| 18 | Inspect checksum after copy | Small CopyObject response omitted SHA-256; HEAD exposed a full-object SHA-256. Multipart UploadPartCopy with target `ChecksumAlgorithm=SHA256` failed 400 `InvalidArgument` (`checksum missing`); without it, copy worked but parts/final exposed no SHA-256. | Provider-specific. Multipart copy requires whole-final fallback verification on this MinIO. |
| 19 | Concurrent conditional Complete from two MPUs to one key | Exactly one 200 and one 412; final bytes matched exactly one contender. Existing final survived a conditional challenge; losing MPU stayed listable and abortable. | AWS documents first-writer success and later 412; 409 is also possible. This is the selected publication primitive. |
| 20 | List incomplete MPU, abort, list again | Count changed 1 -> 0 immediately. Compose config declares stale cleanup every 6 h with 168 h expiry. The seven-day timed expiry was not time-advanced or claimed as observed. | Explicit abort is standard. AWS recommends `AbortIncompleteMultipartUpload` lifecycle as a backstop; exact MinIO environment settings are provider-specific. |

## Additional multipart-copy evidence

To test the strongest form of Option B, a verified 6 MiB + 12,345 B temporary object was copied by
two `UploadPartCopy` ranges into a second MPU at the final key.

```python
s3.upload_part_copy(
    Bucket=bucket,
    Key=final_key,
    UploadId=publication_upload_id,
    PartNumber=number,
    CopySource={"Bucket": bucket, "Key": temp_key},
    CopySourceRange=f"bytes={start}-{end}",
    CopySourceIfMatch=temp_etag,
)
```

- With `ChecksumAlgorithm="SHA256"` on the destination MPU, the first copy part failed with 400
  `InvalidArgument` because MinIO supplied no copy-part checksum.
- Without a checksum algorithm, multipart copy and conditional final Complete succeeded and bytes
  matched, but ListParts, completion, and HEAD exposed no SHA-256.
- A second conditional completion returned 412 and preserved the first final object.
- Concurrent multipart-copy publications returned one 200 and one 412.

This proves that safe create-only multipart copy is possible only by using another conditional
Complete, but it does not supply an integrity shortcut and materially enlarges lifecycle/I/O. It was
therefore rejected for M2.

## Checksum and ETag interpretation

For the two-part upload, MinIO returned:

```text
ChecksumSHA256: /geGR3KckWw4nZlpxlCcRgIfvsNR2Gn/BSEueP65qy8=-2
ETag:           "1665a2d00f54611e8a71f4316a657340-2"
ChecksumType:   COMPOSITE
```

The checksum equals `base64(SHA256(raw_sha256(part1) || raw_sha256(part2))) + "-2"`. The actual
whole-file digest is different. RoboLake may use the composite value to diagnose exact part
composition but must stream the final bytes to establish `Blob.sha256`.

## Completion response contract

The live MinIO success response was parsed by botocore as a normal completion result. The probe did
not induce an HTTP 200 response whose XML body contained `<Error>`. AWS explicitly documents that
CompleteMultipartUpload can send initial status 200 and later embed an error in the response body;
AWS SDKs parse and surface that condition. RoboLake therefore treats an adapter/SDK parsed success
result—not transport status—as the completion input. Implementation contract tests must feed a fake
200-plus-embedded-error response through the adapter boundary and prove the session stays
`COMPLETING` and the Blob remains non-`AVAILABLE`.

The live concurrent MinIO probe produced 200/412 and did not produce 409. AWS documents that a 409
conditional-completion conflict requires a new MPU and re-upload of every part. M2 follows that
portable rule: after one final-key reconciliation finds no object, the previous upload ID is never
resumed.

Official references:

- [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- [AWS CompleteMultipartUpload API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html)
- [AWS multipart overview](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)
- [AWS checksum types](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html)
- [AWS AbortMultipartUpload](https://docs.aws.amazon.com/AmazonS3/latest/API/API_AbortMultipartUpload.html)
- [AWS multipart limits](https://docs.aws.amazon.com/AmazonS3/latest/userguide/qfacts.html)
- [MinIO limits](https://min.io/docs/minio/kubernetes/openshift/operations/concepts/thresholds.html)

## Unproven items

- Packet-level loss during the Complete response was represented by deliberately discarding a
  successful response. Implementation tests must add a faulting reverse proxy or transport stub.
- An embedded-error-in-200 response was not induced live; it requires an adapter/SDK fake in normal
  CI as specified above.
- MinIO 409 was not observed; the restart rule comes from the AWS API contract and must be fault-
  injected in CI.
- Seven-day stale-upload expiry was configured but not observed by waiting or changing server time.
- AWS was not contacted by these probes; AWS portability statements come from official API/user
  documentation and require a future live AWS contract run before claiming AWS as tested.
- Multi-gigabyte throughput and memory were not measured in this design task.
