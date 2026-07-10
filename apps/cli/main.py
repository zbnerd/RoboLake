"""RoboLake Typer command-line composition root."""

from pathlib import Path
from typing import Annotated

import typer
from robolake import __version__
from robolake.infrastructure.example_dataset import (
    DestinationNotEmptyError,
    generate_synthetic_dataset,
)

app = typer.Typer(
    name="robolake",
    help="Robot dataset transfer and registry platform.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
example_app = typer.Typer(help="Create synthetic data for local development.")
app.add_typer(example_app, name="example")


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
