"""RoboLake Typer command-line composition root."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import httpx
import typer
from robolake import __version__
from robolake.application.ports import ControlPlanePort
from robolake.application.pull import PullWorkflow
from robolake.application.push import PushWorkflow
from robolake.infrastructure.api_client import RoboLakeApiClient
from robolake.infrastructure.atomic_tree import AtomicTreeDownloader
from robolake.infrastructure.example_dataset import (
    DestinationNotEmptyError,
    generate_synthetic_dataset,
)
from robolake.infrastructure.http_transfer import HttpByteTransfer
from robolake.infrastructure.scanner import FileSystemScanner
from robolake.infrastructure.settings import Settings

from apps.cli.commands.datasets import manifest_command, pull_command, push_command, status_command
from apps.cli.output import render_progress


@dataclass(frozen=True, slots=True)
class CliServices:
    control_plane: ControlPlanePort
    push_workflow: PushWorkflow
    pull_workflow: PullWorkflow
    close: Callable[[], None]


def build_cli_services(settings: Settings) -> CliServices:
    control_http = httpx.Client(
        base_url=settings.api_url,
        timeout=httpx.Timeout(30.0),
    )
    transfer_http = httpx.Client(
        timeout=httpx.Timeout(connect=30.0, read=300.0, write=None, pool=30.0)
    )
    control = RoboLakeApiClient(control_http)
    transfer = HttpByteTransfer(transfer_http, chunk_size=settings.stream_chunk_bytes)
    workflow = PushWorkflow(
        FileSystemScanner(
            max_single_put_bytes=settings.max_single_put_bytes,
            chunk_size=settings.stream_chunk_bytes,
        ),
        control,
        transfer,
        render_progress,
    )
    pull_workflow = PullWorkflow(control, AtomicTreeDownloader(transfer))

    def close() -> None:
        transfer_http.close()
        control.close()

    return CliServices(control, workflow, pull_workflow, close)


app = typer.Typer(
    name="robolake",
    help="Robot dataset transfer and registry platform.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
example_app = typer.Typer(help="Create synthetic data for local development.")
app.add_typer(example_app, name="example")


@app.callback()
def root(context: typer.Context) -> None:
    """Initialize owned clients only when callers have not injected services."""
    if context.obj is None:
        services = build_cli_services(Settings())
        context.obj = services
        context.call_on_close(services.close)


@app.command("version")
def version_command() -> None:
    """Print the installed RoboLake version."""
    typer.echo(__version__)


@example_app.command("generate")
def generate_example(
    destination: Annotated[
        Path,
        typer.Argument(help="New or empty directory to populate with synthetic files."),
    ],
    seed: Annotated[int, typer.Option(help="Deterministic synthetic data seed.")] = 0,
) -> None:
    """Generate a deterministic dataset containing no real robot data."""
    try:
        generated = generate_synthetic_dataset(destination, seed=seed)
    except DestinationNotEmptyError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=2) from None

    typer.echo(
        f"Generated {len(generated.relative_files)} synthetic files "
        f"({generated.total_bytes} bytes) in {generated.root}"
    )


app.command("push")(push_command)
app.command("status")(status_command)
app.command("manifest")(manifest_command)
app.command("pull")(pull_command)
