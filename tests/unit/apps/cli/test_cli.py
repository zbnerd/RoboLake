from pathlib import Path

import pytest
from apps.cli.main import app
from typer.testing import CliRunner

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_root_help_lists_m0_commands_and_no_transfer_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Robot dataset transfer and registry platform" in result.output
    assert "example" in result.output
    assert "version" in result.output
    assert "push" not in result.output
    assert "pull" not in result.output


def test_version_reports_project_version() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_example_generate_creates_dataset(tmp_path: Path) -> None:
    destination = tmp_path / "example"

    result = runner.invoke(app, ["example", "generate", str(destination), "--seed", "7"])

    assert result.exit_code == 0
    assert "4 synthetic files" in result.output
    assert (destination / "camera/front/frame-000001.bin").is_file()


def test_example_generate_refuses_non_empty_destination(tmp_path: Path) -> None:
    destination = tmp_path / "example"
    destination.mkdir()
    existing = destination / "existing.txt"
    existing.write_text("keep", encoding="utf-8")

    result = runner.invoke(app, ["example", "generate", str(destination)])

    assert result.exit_code == 2
    assert "destination is not empty" in result.output
    assert existing.read_text(encoding="utf-8") == "keep"
