"""Database enforcement for immutable registry behavior."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def _insert_sealed_version(
    connection: Connection,
    *,
    name: str,
    ordinal: int = 0,
    logical_bytes: int = 0,
    unique_blob_count: int = 1,
    unique_blob_bytes: int = 0,
) -> tuple[UUID, UUID, UUID]:
    dataset_id = uuid4()
    version_id = uuid4()
    blob_id = uuid4()
    now = datetime.now(UTC)
    digest = uuid4().hex * 2
    manifest_digest = uuid4().hex * 2
    connection.execute(
        text("INSERT INTO datasets (id, name, created_at) VALUES (:id, :name, :now)"),
        {"id": dataset_id, "name": name, "now": now},
    )
    connection.execute(
        text(
            "INSERT INTO blobs "
            "(id, sha256, size_bytes, object_key, state, created_at) "
            "VALUES (:id, :sha, 0, :key, 'PENDING', :now)"
        ),
        {
            "id": blob_id,
            "sha": digest,
            "key": f"blobs/sha256/{digest[:2]}/{digest[2:4]}/{digest}",
            "now": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO dataset_versions "
            "(id, dataset_id, version_number, manifest_schema_version, manifest_bytes, "
            "manifest_sha256, state, file_count, logical_bytes, unique_blob_count, "
            "unique_blob_bytes, created_at, sealed_at) "
            "VALUES (:id, :dataset_id, 1, 1, :manifest, :sha, 'DRAFT', 1, "
            ":logical_bytes, :unique_blob_count, :unique_blob_bytes, :now, NULL)"
        ),
        {
            "id": version_id,
            "dataset_id": dataset_id,
            "manifest": b'{"schema_version":1,"entries":[]}',
            "sha": manifest_digest,
            "logical_bytes": logical_bytes,
            "unique_blob_count": unique_blob_count,
            "unique_blob_bytes": unique_blob_bytes,
            "now": now,
        },
    )
    connection.execute(
        text(
            "INSERT INTO dataset_entries "
            "(dataset_version_id, manifest_ordinal, relative_path, blob_id) "
            "VALUES (:version_id, :ordinal, 'empty.bin', :blob_id)"
        ),
        {"version_id": version_id, "ordinal": ordinal, "blob_id": blob_id},
    )
    connection.execute(
        text("UPDATE dataset_versions SET sealed_at = :now WHERE id = :id"),
        {"id": version_id, "now": now},
    )
    return dataset_id, version_id, blob_id


def test_registration_can_insert_entries_then_seal_in_one_transaction(
    database_connection: Connection,
) -> None:
    _, version_id, _ = _insert_sealed_version(database_connection, name="test/registration")

    assert database_connection.execute(
        text("SELECT sealed_at IS NOT NULL FROM dataset_versions WHERE id = :id"),
        {"id": version_id},
    ).scalar_one()


def test_sealing_rejects_noncontiguous_manifest_ordinals(
    database_connection: Connection,
) -> None:
    with pytest.raises(IntegrityError):
        _insert_sealed_version(
            database_connection,
            name=f"test/noncontiguous-{uuid4()}",
            ordinal=1,
        )


@pytest.mark.parametrize(
    "totals",
    [
        {"logical_bytes": 1},
        {"unique_blob_count": 0},
        {"unique_blob_bytes": 1},
    ],
)
def test_sealing_rejects_summary_totals_that_disagree_with_entries(
    database_connection: Connection, totals: dict[str, int]
) -> None:
    with pytest.raises(IntegrityError):
        _insert_sealed_version(
            database_connection,
            name=f"test/summary-{uuid4()}",
            **totals,
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE dataset_entries SET relative_path = 'changed.bin' "
        "WHERE dataset_version_id = :version_id",
        "DELETE FROM dataset_entries WHERE dataset_version_id = :version_id",
        "INSERT INTO dataset_entries "
        "(dataset_version_id, manifest_ordinal, relative_path, blob_id) "
        "VALUES (:version_id, 1, 'added.bin', :blob_id)",
    ],
)
def test_sealed_version_rejects_every_entry_mutation(
    database_connection: Connection, statement: str
) -> None:
    _, version_id, blob_id = _insert_sealed_version(
        database_connection, name=f"test/sealed-{uuid4()}"
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(text(statement), {"version_id": version_id, "blob_id": blob_id})


@pytest.mark.parametrize(
    "assignment",
    [
        "manifest_bytes = 'changed'::bytea",
        "manifest_sha256 = repeat('a', 64)",
        "file_count = file_count + 1",
        "version_number = version_number + 1",
        "sealed_at = sealed_at + interval '1 second'",
    ],
)
def test_sealed_version_rejects_content_identity_mutation(
    database_connection: Connection, assignment: str
) -> None:
    _, version_id, _ = _insert_sealed_version(database_connection, name=f"test/immutable-{uuid4()}")

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(f"UPDATE dataset_versions SET {assignment} WHERE id = :version_id"),
            {"version_id": version_id},
        )


@pytest.mark.parametrize(
    "assignment",
    [
        "sha256 = repeat('f', 64)",
        "size_bytes = size_bytes + 1",
        "object_key = object_key || '-changed'",
        "state = 'AVAILABLE'",
    ],
)
def test_blob_rejects_identity_mutation_and_illegal_state_jump(
    database_connection: Connection, assignment: str
) -> None:
    _, _, blob_id = _insert_sealed_version(database_connection, name=f"test/blob-guard-{uuid4()}")

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(f"UPDATE blobs SET {assignment} WHERE id = :blob_id"),
            {"blob_id": blob_id},
        )


def test_upload_session_rejects_illegal_state_jump(database_connection: Connection) -> None:
    _, version_id, blob_id = _insert_sealed_version(
        database_connection, name=f"test/session-guard-{uuid4()}"
    )
    session_id = uuid4()
    now = datetime.now(UTC)
    database_connection.execute(
        text(
            "INSERT INTO upload_sessions "
            "(id, blob_id, initiating_version_id, strategy, state, created_at, last_activity_at) "
            "VALUES (:id, :blob_id, :version_id, 'SINGLE_PUT', 'CREATED', :now, :now)"
        ),
        {"id": session_id, "blob_id": blob_id, "version_id": version_id, "now": now},
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text("UPDATE upload_sessions SET state = 'COMPLETED' WHERE id = :id"),
            {"id": session_id},
        )


def test_blob_key_must_match_its_content_digest(database_connection: Connection) -> None:
    with pytest.raises(IntegrityError):
        database_connection.execute(
            text(
                "INSERT INTO blobs "
                "(id, sha256, size_bytes, object_key, state, created_at) "
                "VALUES (:id, :sha, 0, 'blobs/not-content-addressed', 'PENDING', :now)"
            ),
            {"id": uuid4(), "sha": uuid4().hex * 2, "now": datetime.now(UTC)},
        )


def test_dataset_version_deletion_is_not_supported(database_connection: Connection) -> None:
    dataset_id = uuid4()
    version_id = uuid4()
    now = datetime.now(UTC)
    database_connection.execute(
        text("INSERT INTO datasets (id, name, created_at) VALUES (:id, :name, :now)"),
        {"id": dataset_id, "name": f"test/no-delete-{dataset_id}", "now": now},
    )
    database_connection.execute(
        text(
            "INSERT INTO dataset_versions "
            "(id, dataset_id, version_number, manifest_schema_version, manifest_bytes, "
            "manifest_sha256, state, file_count, logical_bytes, unique_blob_count, "
            "unique_blob_bytes, created_at, sealed_at) "
            "VALUES (:id, :dataset_id, 1, 1, :manifest, :sha, 'DRAFT', 0, 0, 0, 0, :now, NULL)"
        ),
        {
            "id": version_id,
            "dataset_id": dataset_id,
            "manifest": b'{"schema_version":1,"entries":[]}',
            "sha": uuid4().hex * 2,
            "now": now,
        },
    )
    database_connection.execute(
        text("UPDATE dataset_versions SET sealed_at = :now WHERE id = :id"),
        {"id": version_id, "now": now},
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text("DELETE FROM dataset_versions WHERE id = :id"), {"id": version_id}
        )


def test_legal_lifecycles_reach_terminal_immutable_states(
    database_connection: Connection,
) -> None:
    _, version_id, blob_id = _insert_sealed_version(
        database_connection, name=f"test/legal-lifecycle-{uuid4()}"
    )
    for state in ("UPLOADING", "VERIFYING", "READY"):
        database_connection.execute(
            text("UPDATE dataset_versions SET state = :state WHERE id = :id"),
            {"id": version_id, "state": state},
        )
    for state in ("UPLOADING", "VERIFYING", "AVAILABLE"):
        database_connection.execute(
            text("UPDATE blobs SET state = :state WHERE id = :id"),
            {"id": blob_id, "state": state},
        )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            text("UPDATE dataset_versions SET failure_detail = 'changed' WHERE id = :id"),
            {"id": version_id},
        )


def test_only_one_active_upload_session_exists_per_blob(
    database_connection: Connection,
) -> None:
    _, version_id, blob_id = _insert_sealed_version(
        database_connection, name=f"test/one-active-session-{uuid4()}"
    )
    now = datetime.now(UTC)
    statement = text(
        "INSERT INTO upload_sessions "
        "(id, blob_id, initiating_version_id, strategy, state, created_at, last_activity_at) "
        "VALUES (:id, :blob_id, :version_id, 'SINGLE_PUT', 'CREATED', :now, :now)"
    )
    database_connection.execute(
        statement,
        {"id": uuid4(), "blob_id": blob_id, "version_id": version_id, "now": now},
    )

    with pytest.raises(IntegrityError):
        database_connection.execute(
            statement,
            {"id": uuid4(), "blob_id": blob_id, "version_id": version_id, "now": now},
        )


def test_unsealed_version_cannot_commit(migrated_database_url: str) -> None:
    engine = create_engine(migrated_database_url)
    dataset_id = uuid4()
    version_id = uuid4()
    now = datetime.now(UTC)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("INSERT INTO datasets (id, name, created_at) VALUES (:id, :name, :now)"),
                {"id": dataset_id, "name": f"test/unsealed-{dataset_id}", "now": now},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_versions "
                    "(id, dataset_id, version_number, manifest_schema_version, manifest_bytes, "
                    "manifest_sha256, state, file_count, logical_bytes, unique_blob_count, "
                    "unique_blob_bytes, created_at, sealed_at) "
                    "VALUES (:id, :dataset_id, 1, 1, :manifest, :sha, 'DRAFT', "
                    "0, 0, 0, 0, :now, NULL)"
                ),
                {
                    "id": version_id,
                    "dataset_id": dataset_id,
                    "manifest": b'{"schema_version":1,"entries":[]}',
                    "sha": "2" * 64,
                    "now": now,
                },
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM dataset_versions WHERE id = :id"), {"id": version_id}
            )
            connection.execute(text("DELETE FROM datasets WHERE id = :id"), {"id": dataset_id})
        engine.dispose()
