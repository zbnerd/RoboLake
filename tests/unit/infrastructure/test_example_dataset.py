from pathlib import Path

import pytest
from robolake.infrastructure.example_dataset import (
    DestinationNotEmptyError,
    generate_synthetic_dataset,
)

pytestmark = pytest.mark.unit


def read_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_generator_is_deterministic_and_creates_nested_synthetic_files(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    first = generate_synthetic_dataset(first_root, seed=7)
    second = generate_synthetic_dataset(second_root, seed=7)

    assert read_tree(first_root) == read_tree(second_root)
    assert {path.as_posix() for path in first.relative_files} == {
        "README.txt",
        "camera/front/frame-000001.bin",
        "metadata/session.json",
        "sensors/imu.csv",
    }
    assert first.total_bytes == second.total_bytes
    assert first.total_bytes > 0


def test_generator_refuses_non_empty_destination(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(DestinationNotEmptyError):
        generate_synthetic_dataset(destination, seed=1)

    assert (destination / "keep.txt").read_text(encoding="utf-8") == "keep me"


def test_generated_content_contains_no_local_path_or_credentials(tmp_path: Path) -> None:
    destination = tmp_path / "synthetic"

    generate_synthetic_dataset(destination, seed=3)

    content = b"\n".join(read_tree(destination).values())
    assert str(tmp_path).encode() not in content
    assert b"password" not in content.lower()
    assert b"secret" not in content.lower()
    assert b"access_key" not in content.lower()
