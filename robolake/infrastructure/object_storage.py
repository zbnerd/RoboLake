"""S3-compatible object-storage configuration and connectivity probe."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from boto3.session import Session
from botocore.config import Config
from botocore.exceptions import ClientError

from robolake.application.contracts import ObjectInfo, PresignedRequest
from robolake.domain.identifiers import Sha256Digest
from robolake.infrastructure.settings import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def create_s3_client(settings: Settings) -> S3Client:
    """Create a path-style SigV4 client compatible with MinIO and Amazon S3."""
    return _create_s3_client(settings, settings.s3_endpoint_url)


def create_public_s3_client(settings: Settings) -> S3Client:
    """Create a signing client whose URLs are reachable by the CLI host."""
    return _create_s3_client(settings, settings.s3_public_endpoint_url)


def _create_s3_client(settings: Settings, endpoint_url: str) -> S3Client:
    return Session().client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@dataclass(frozen=True)
class ObjectStorageHealthProbe:
    """Verify that the configured blob bucket is reachable."""

    client: S3Client
    bucket: str
    name: str = field(init=False, default="object_storage")

    def check(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


@dataclass(frozen=True)
class S3ObjectStore:
    """S3 control adapter using separate internal and public presigning clients."""

    internal_client: S3Client
    public_client: S3Client
    bucket: str

    def head(self, object_key: str) -> ObjectInfo | None:
        """Read provider-owned object size and system SHA-256 attestation."""
        try:
            response = self.internal_client.head_object(
                Bucket=self.bucket,
                Key=object_key,
                ChecksumMode="ENABLED",
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        checksum = _parse_provider_checksum(response.get("ChecksumSHA256"))
        return ObjectInfo(
            size_bytes=response["ContentLength"],
            checksum_sha256=checksum,
            etag=response.get("ETag"),
        )

    def presign_put(
        self,
        object_key: str,
        size_bytes: int,
        checksum_base64: str,
        expires_seconds: int,
    ) -> PresignedRequest:
        """Sign one create-only, size- and SHA-256-bound PUT request."""
        headers = {
            "If-None-Match": "*",
            "Content-Length": str(size_bytes),
            "x-amz-checksum-sha256": checksum_base64,
        }
        url = self.public_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "IfNoneMatch": "*",
                "ContentLength": size_bytes,
                "ChecksumSHA256": checksum_base64,
            },
            ExpiresIn=expires_seconds,
            HttpMethod="PUT",
        )
        return PresignedRequest(url=url, headers=headers)

    def iter_bytes(self, object_key: str, chunk_size: int) -> Iterator[bytes]:
        """Stream stored bytes and close the provider body on every exit path."""
        response = self.internal_client.get_object(Bucket=self.bucket, Key=object_key)
        body = response["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size=chunk_size):
                if chunk:
                    yield chunk
        finally:
            body.close()

    def presign_get(self, object_key: str, expires_seconds: int) -> PresignedRequest:
        """Sign one short-lived GET capability using the caller-reachable endpoint."""
        url = self.public_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expires_seconds,
            HttpMethod="GET",
        )
        return PresignedRequest(url=url, headers={})


def _parse_provider_checksum(value: str | None) -> Sha256Digest | None:
    if value is None:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 32:
        return None
    return Sha256Digest.parse(raw.hex())
