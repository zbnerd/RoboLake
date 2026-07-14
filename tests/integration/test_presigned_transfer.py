"""Pinned-MinIO evidence for M1 conditional single-PUT semantics."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from robolake.application.registry import RegistryService
from robolake.application.transfers import TransferService
from robolake.domain.errors import StoredObjectMismatchError
from robolake.domain.identifiers import DatasetName, RelativePath, Sha256Digest, object_key_for
from robolake.domain.lifecycle import VersionState
from robolake.domain.manifest import Manifest, ManifestEntry
from robolake.domain.records import VersionRecord
from robolake.infrastructure.object_storage import (
    S3ObjectStore,
    create_public_s3_client,
    create_s3_client,
)
from robolake.infrastructure.settings import Settings
from robolake.infrastructure.store import SqlAlchemyStore
from sqlalchemy import Connection
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


@pytest.fixture
def live_object_store() -> S3ObjectStore:
    settings = Settings()
    internal = create_s3_client(settings)
    public = create_public_s3_client(settings)
    return S3ObjectStore(internal, public, settings.s3_bucket)


def _identity(data: bytes) -> tuple[Sha256Digest, str]:
    digest = Sha256Digest.parse(hashlib.sha256(data).hexdigest())
    return digest, object_key_for(digest)


def test_minio_conditional_put_creates_once_and_preserves_first_bytes(
    live_object_store: S3ObjectStore,
) -> None:
    data = f"synthetic-create-{uuid4()}".encode()
    digest, key = _identity(data)
    request = live_object_store.presign_put(key, len(data), digest.checksum_base64, 60)

    first = httpx.put(request.url, content=data, headers=request.headers, timeout=10)
    duplicate = httpx.put(request.url, content=data, headers=request.headers, timeout=10)
    stale_overwrite = httpx.put(
        request.url,
        content=b"z" * len(data),
        headers=request.headers,
        timeout=10,
    )

    assert first.status_code == 200
    assert duplicate.status_code == 412
    assert stale_overwrite.status_code == 412
    info = live_object_store.head(key)
    assert info is not None
    assert info.size_bytes == len(data)
    assert info.checksum_sha256 == digest
    assert b"".join(live_object_store.iter_bytes(key, 3)) == data


def test_minio_rejects_body_that_does_not_match_signed_checksum(
    live_object_store: S3ObjectStore,
) -> None:
    expected = f"synthetic-good-{uuid4()}".encode()
    wrong = b"x" * len(expected)
    digest, key = _identity(expected)
    request = live_object_store.presign_put(key, len(expected), digest.checksum_base64, 60)

    response = httpx.put(request.url, content=wrong, headers=request.headers, timeout=10)

    assert response.status_code == 400
    assert live_object_store.head(key) is None


def test_minio_simultaneous_conditional_creates_converge_on_one_object(
    live_object_store: S3ObjectStore,
) -> None:
    data = f"synthetic-race-{uuid4()}".encode()
    digest, key = _identity(data)
    request = live_object_store.presign_put(key, len(data), digest.checksum_base64, 60)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                httpx.put,
                request.url,
                content=data,
                headers=request.headers,
                timeout=10,
            )
            for _ in range(2)
        ]
        statuses = sorted(future.result().status_code for future in futures)

    assert statuses == [200, 412]
    assert b"".join(live_object_store.iter_bytes(key, 1024)) == data


def _services(
    database_connection: Connection, objects: S3ObjectStore
) -> tuple[RegistryService, TransferService]:
    sessions = sessionmaker(
        bind=database_connection,
        class_=Session,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    store = SqlAlchemyStore(sessions)
    return RegistryService(store), TransferService(store, objects)


def _register_single_blob(
    registry: RegistryService, data: bytes, key_suffix: str
) -> tuple[Manifest, VersionRecord]:
    digest, _ = _identity(data)
    dataset = registry.create_dataset(
        DatasetName.parse(f"test/presign-{key_suffix}"), f"dataset-{key_suffix}"
    )
    manifest = Manifest.build([ManifestEntry(RelativePath.parse("blob.bin"), len(data), digest)])
    version = registry.register_version(dataset.id, manifest, f"version-{key_suffix}")
    return manifest, version


def test_matching_existing_object_reconciles_and_poisoned_key_is_never_overwritten(
    live_object_store: S3ObjectStore, database_connection: Connection
) -> None:
    registry, transfers = _services(database_connection, live_object_store)
    matching_data = f"synthetic-reconcile-{uuid4()}".encode()
    suffix = uuid4().hex
    matching_manifest, matching_version = _register_single_blob(registry, matching_data, suffix)
    matching_digest = matching_manifest.entries[0].sha256
    request = live_object_store.presign_put(
        object_key_for(matching_digest),
        len(matching_data),
        matching_digest.checksum_base64,
        60,
    )
    assert httpx.put(request.url, content=matching_data, headers=request.headers).status_code == 200

    reconciled = transfers.prepare_upload(matching_version.id, matching_digest, f"upload-{suffix}")
    assert reconciled.available is True
    assert registry.status(matching_version.id).content.available_blob_count == 1
    assert transfers.finalize(matching_version.id).state is VersionState.READY

    expected = f"synthetic-expected-{uuid4()}".encode()
    wrong = b"z" * len(expected)
    poisoned_suffix = uuid4().hex
    poisoned_manifest, poisoned_version = _register_single_blob(registry, expected, poisoned_suffix)
    expected_digest = poisoned_manifest.entries[0].sha256
    wrong_digest, _ = _identity(wrong)
    poisoned_key = object_key_for(expected_digest)
    live_object_store.internal_client.put_object(
        Bucket=live_object_store.bucket,
        Key=poisoned_key,
        Body=wrong,
        ChecksumSHA256=wrong_digest.checksum_base64,
    )

    with pytest.raises(StoredObjectMismatchError):
        transfers.prepare_upload(
            poisoned_version.id,
            expected_digest,
            f"upload-{poisoned_suffix}",
        )

    poisoned_status = registry.status(poisoned_version.id)
    assert poisoned_status.version.state is VersionState.FAILED
    assert poisoned_status.content.available_blob_count == 0
    assert poisoned_status.blocking_failure_code is not None
    assert b"".join(live_object_store.iter_bytes(poisoned_key, 1024)) == wrong


def test_minio_get_started_before_expiry_finishes_but_new_request_is_rejected(
    live_object_store: S3ObjectStore,
) -> None:
    data = f"synthetic-expiry-{uuid4()}".encode() * 4096
    digest, key = _identity(data)
    put = live_object_store.presign_put(key, len(data), digest.checksum_base64, 60)
    assert httpx.put(put.url, content=data, headers=put.headers, timeout=10).status_code == 200
    # SigV4 signing timestamps have one-second precision. A one-second TTL can
    # therefore have almost no usable lifetime when generated near a boundary.
    get = live_object_store.presign_get(key, 3)

    with httpx.stream("GET", get.url, timeout=10) as response:
        assert response.status_code == 200
        time.sleep(4)
        received = b"".join(response.iter_bytes())

    assert received == data
    assert httpx.get(get.url, timeout=10).status_code == 403
