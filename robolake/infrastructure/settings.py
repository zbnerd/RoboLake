"""Environment-backed RoboLake configuration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or local env files."""

    model_config = SettingsConfigDict(
        env_file=(".env.example", ".env"),
        env_prefix="ROBOLAKE_",
        extra="ignore",
        frozen=True,
        str_strip_whitespace=True,
    )

    environment: str = "development"
    database_url: str = Field(min_length=1)
    s3_endpoint_url: str = Field(min_length=1)
    s3_public_endpoint_url: str = Field(min_length=1)
    s3_access_key: str = Field(min_length=1)
    s3_secret_key: SecretStr
    s3_bucket: str = Field(min_length=1)
    s3_region: str = Field(min_length=1)
    api_url: str = Field(default="http://localhost:18000", min_length=1)
    stream_chunk_bytes: int = Field(default=1_048_576, gt=0)
    max_single_put_bytes: int = Field(default=5_000_000_000, gt=0, le=5_000_000_000)
    presigned_url_ttl_seconds: int = Field(default=900, gt=0)
