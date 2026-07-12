"""Installed Typer commands through the real FastAPI/PostgreSQL/MinIO control plane."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx
import pytest
from apps.api.main import build_api_services, create_app
from apps.cli.main import CliServices, app
from apps.cli.output import render_progress
from fastapi.testclient import TestClient
from robolake.application.ports import ControlPlanePort
from robolake.application.pull import PullWorkflow
from robolake.application.push import PushWorkflow
from robolake.infrastructure.api_client import RoboLakeApiClient
from robolake.infrastructure.atomic_tree import AtomicTreeDownloader
from robolake.infrastructure.http_transfer import HttpByteTransfer
from robolake.infrastructure.scanner import FileSystemScanner
from robolake.infrastructure.settings import Settings
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


def _inventory(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_cli_push_status_manifest_pull_real_vertical_slice(
    migrated_database_url: str, tmp_path: Path
) -> None:
    settings = Settings(database_url=migrated_database_url)
    api_services = build_api_services(settings)
    source = tmp_path / "source"
    (source / "camera").mkdir(parents=True)
    (source / "camera" / "frame.bin").write_bytes(f"frame-{uuid4()}".encode())
    (source / "sensor.bin").write_bytes(f"sensor-{uuid4()}".encode())
    output = tmp_path / "restored"
    dataset = f"test/cli-{uuid4().hex}"
    runner = CliRunner()

    with TestClient(create_app(services=api_services)) as test_client:
        control = RoboLakeApiClient(cast(httpx.Client, test_client))
        transfer_client = httpx.Client(timeout=10)
        transfer = HttpByteTransfer(transfer_client, chunk_size=2)
        services = CliServices(
            control_plane=cast(ControlPlanePort, control),
            push_workflow=PushWorkflow(
                FileSystemScanner(chunk_size=2),
                control,
                transfer,
                render_progress,
            ),
            pull_workflow=PullWorkflow(control, AtomicTreeDownloader(transfer)),
            close=lambda: None,
        )

        pushed = runner.invoke(
            app,
            ["push", str(source), "--dataset", dataset],
            obj=services,
        )
        reference = f"{dataset}@v1"
        status = runner.invoke(app, ["status", reference], obj=services)
        manifest = runner.invoke(app, ["manifest", reference], obj=services)
        pulled = runner.invoke(
            app,
            ["pull", reference, "--output", str(output)],
            obj=services,
        )
        transfer_client.close()

    assert pushed.exit_code == 0, pushed.output
    assert pushed.stdout == f"READY {reference}\n"
    assert status.exit_code == 0, status.output
    assert "State: READY" in status.stdout
    assert manifest.exit_code == 0, manifest.output
    assert manifest.stdout_bytes.startswith(b'{"schema_version":1,"entries":[')
    assert pulled.exit_code == 0, pulled.output
    assert _inventory(output) == _inventory(source)
