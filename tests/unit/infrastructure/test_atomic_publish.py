"""Platform no-replace directory publication tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from robolake.domain.errors import OutputExistsError, UnsupportedFilesystemError
from robolake.infrastructure.atomic_publish import publish_no_replace


@pytest.mark.skipif(sys.platform != "linux", reason="Linux primitive contract")
def test_linux_publish_moves_directory_without_replacement(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    output = tmp_path / "output"
    staging.mkdir()
    (staging / "winner.txt").write_text("new", encoding="utf-8")

    publish_no_replace(staging, output)

    assert not staging.exists()
    assert (output / "winner.txt").read_text(encoding="utf-8") == "new"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux primitive contract")
def test_linux_publish_refuses_existing_output_and_preserves_both_trees(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    output = tmp_path / "output"
    staging.mkdir()
    output.mkdir()
    (staging / "candidate.txt").write_text("candidate", encoding="utf-8")
    (output / "winner.txt").write_text("winner", encoding="utf-8")

    with pytest.raises(OutputExistsError):
        publish_no_replace(staging, output)

    assert (staging / "candidate.txt").read_text(encoding="utf-8") == "candidate"
    assert (output / "winner.txt").read_text(encoding="utf-8") == "winner"


def test_publish_fails_closed_on_unsupported_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(sys, "platform", "unsupported")

    with pytest.raises(UnsupportedFilesystemError):
        publish_no_replace(staging, tmp_path / "output")

    assert staging.exists()
