import pytest
from robolake.infrastructure.database import create_database_engine
from robolake.infrastructure.object_storage import create_s3_client
from robolake.infrastructure.settings import Settings
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_postgresql_is_reachable_at_migration_head() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "20260714_0003"
            )
    finally:
        engine.dispose()


def test_minio_supports_bucket_probe_and_multipart_abort() -> None:
    settings = Settings()
    client = create_s3_client(settings)
    object_key = f"blobs/sha256/00/00/{'0' * 64}"

    try:
        head_response = client.head_bucket(Bucket=settings.s3_bucket)
        assert head_response["ResponseMetadata"]["HTTPStatusCode"] == 200

        create_response = client.create_multipart_upload(
            Bucket=settings.s3_bucket,
            Key=object_key,
        )
        upload_id = create_response["UploadId"]
        abort_response = client.abort_multipart_upload(
            Bucket=settings.s3_bucket,
            Key=object_key,
            UploadId=upload_id,
        )
        assert abort_response["ResponseMetadata"]["HTTPStatusCode"] == 204
    finally:
        client.close()
