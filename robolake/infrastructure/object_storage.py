"""S3-compatible object-storage configuration and connectivity probe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from boto3.session import Session
from botocore.config import Config

from robolake.infrastructure.settings import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def create_s3_client(settings: Settings) -> S3Client:
    """Create a path-style SigV4 client compatible with MinIO and Amazon S3."""
    return Session().client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
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
