"""Establish the M0 migration baseline.

Revision ID: 20260710_0001
Revises:
Create Date: 2026-07-10
"""

from collections.abc import Sequence

revision: str = "20260710_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create no domain tables until the M2 registry milestone."""


def downgrade() -> None:
    """Remove no domain tables from the M0 baseline."""
