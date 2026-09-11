"""Dataset push, status, and manifest commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Protocol, cast

import typer
from robolake.application.ports import ControlPlanePort
from robolake.application.pull import PullWorkflow
from robolake.application.push import PushWorkflow
from robolake.domain.errors import RoboLakeError
from robolake.domain.identifiers import DatasetName, DatasetReference

from apps.cli.output import exit_for_error, render_error


class _CliServices(Protocol):
    control_plane: ControlPlanePort
    push_workflow: PushWorkflow
    pull_workflow: PullWorkflow


def _services(context: typer.Context) -> _CliServices:
    return cast(_CliServices, context.obj)


def push_command(
    context: typer.Context,
    source: Annotated[Path, typer.Argument(help="Local dataset directory to scan and publish.")],
    dataset: Annotated[
        str,
        typer.Option("--dataset", help="Logical Dataset name, for example demo/pick-place."),
    ],
) -> None:
    """Push one local regular-file tree as an immutable DatasetVersion."""
    try:
        result = _services(context).push_workflow.run(source, DatasetName.parse(dataset))
    except RoboLakeError as error:
        render_error(error)
        raise typer.Exit(exit_for_error(error)) from None
    typer.echo(f"Scanned {result.file_count} files ({result.logical_bytes} bytes)", err=True)
    typer.echo(f"{result.state.value} {result.reference}")


def status_command(
    context: typer.Context,
    reference: Annotated[
        str,
        typer.Argument(help="DatasetVersion reference in DATASET@vN form."),
    ],
) -> None:
    """Show logical snapshot and deduplicated content progress."""
    try:
        control = _services(context).control_plane
        version = control.resolve(DatasetReference.parse(reference))
        value = control.status(version.id)
    except RoboLakeError as error:
        render_error(error)
        raise typer.Exit(exit_for_error(error)) from None
    empty = "yes" if value.version.is_empty else "no"
    typer.echo(
        f"State: {value.version.state.value}, "
        f"Snapshot: {value.snapshot.ready_file_count}/{value.snapshot.file_count} files "
        f"({value.snapshot.ready_logical_bytes}/{value.snapshot.logical_bytes} logical bytes), "
        f"Content: {value.content.available_blob_count}/{value.content.unique_blob_count} blobs "
        f"({value.content.available_blob_bytes}/{value.content.unique_blob_bytes} unique bytes), "
        f"Empty: {empty}"
    )


def manifest_command(
    context: typer.Context,
    reference: Annotated[
        str,
        typer.Argument(help="DatasetVersion reference in DATASET@vN form."),
    ],
) -> None:
    """Write exact canonical manifest bytes to stdout."""
    try:
        control = _services(context).control_plane
        version = control.resolve(DatasetReference.parse(reference))
        manifest = control.manifest(version.id)
    except RoboLakeError as error:
        render_error(error)
        raise typer.Exit(exit_for_error(error)) from None
    sys.stdout.buffer.write(manifest.canonical_bytes)
    sys.stdout.buffer.flush()


def pull_command(
    context: typer.Context,
    reference: Annotated[
        str,
        typer.Argument(help="DatasetVersion reference in DATASET@vN form."),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", help="New local directory to publish after verification."),
    ],
) -> None:
    """Pull and atomically reconstruct one READY DatasetVersion."""
    try:
        result = _services(context).pull_workflow.run(DatasetReference.parse(reference), output)
    except RoboLakeError as error:
        render_error(error)
        raise typer.Exit(exit_for_error(error)) from None
    typer.echo(f"RESTORED {result.reference} -> {output}")
