import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on the application's asyncio backend only."""
    return "asyncio"
