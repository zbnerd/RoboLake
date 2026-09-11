import pytest
from pydantic import ValidationError
from robolake.infrastructure.settings import Settings

pytestmark = pytest.mark.unit


def test_settings_read_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "ROBOLAKE_DATABASE_URL",
        "postgresql+psycopg://example-user:example-password@localhost:15432/example-db",
    )
    monkeypatch.setenv("ROBOLAKE_S3_ENDPOINT_URL", "http://localhost:19000")
    monkeypatch.setenv("ROBOLAKE_S3_PUBLIC_ENDPOINT_URL", "http://localhost:19000")
    monkeypatch.setenv("ROBOLAKE_S3_ACCESS_KEY", "synthetic-access-key")
    monkeypatch.setenv("ROBOLAKE_S3_SECRET_KEY", "synthetic-secret-key")
    monkeypatch.setenv("ROBOLAKE_S3_BUCKET", "synthetic-bucket")
    monkeypatch.setenv("ROBOLAKE_S3_REGION", "us-test-1")

    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("/example-db")
    assert settings.s3_endpoint_url == "http://localhost:19000"
    assert settings.s3_public_endpoint_url == "http://localhost:19000"
    assert settings.s3_access_key == "synthetic-access-key"
    assert settings.s3_secret_key.get_secret_value() == "synthetic-secret-key"
    assert settings.s3_bucket == "synthetic-bucket"
    assert settings.s3_region == "us-test-1"
    assert settings.api_url == "http://localhost:18000"
    assert settings.stream_chunk_bytes == 1_048_576
    assert settings.max_single_put_bytes == 5_000_000_000
    assert settings.presigned_url_ttl_seconds == 900
    assert "synthetic-secret-key" not in repr(settings)


@pytest.mark.parametrize(
    "override",
    [
        {"stream_chunk_bytes": 0},
        {"max_single_put_bytes": 0},
        {"max_single_put_bytes": 5_000_000_001},
        {"presigned_url_ttl_seconds": 0},
    ],
)
def test_settings_reject_invalid_operational_limits(override: dict[str, int]) -> None:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://user:pass@localhost/db",
        "s3_endpoint_url": "http://localhost:9000",
        "s3_public_endpoint_url": "http://localhost:9000",
        "s3_access_key": "synthetic-key",
        "s3_secret_key": "synthetic-secret",
        "s3_bucket": "synthetic-bucket",
        "s3_region": "us-test-1",
        **override,
    }

    with pytest.raises(ValidationError):
        Settings.model_validate(values)
