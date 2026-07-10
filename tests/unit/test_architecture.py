import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

FORBIDDEN_IMPORTS = {"boto3", "botocore", "fastapi", "minio", "sqlalchemy", "typer"}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def test_modular_monolith_uses_approved_top_level_boundaries() -> None:
    assert Path("apps/api").is_dir()
    assert Path("apps/cli").is_dir()
    assert Path("robolake/domain").is_dir()
    assert Path("robolake/application").is_dir()
    assert Path("robolake/infrastructure").is_dir()
    assert not Path("src/robolake").exists()


@pytest.mark.parametrize(
    "boundary",
    [Path("robolake/domain"), Path("robolake/application")],
)
def test_domain_and_application_do_not_import_frameworks(boundary: Path) -> None:
    violations: dict[str, set[str]] = {}
    for path in boundary.rglob("*.py"):
        forbidden = imported_roots(path) & FORBIDDEN_IMPORTS
        if forbidden:
            violations[str(path)] = forbidden

    assert violations == {}
