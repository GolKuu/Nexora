"""Add persisted DCF display preferences.

Revision ID: 9b52e8d3f612
Revises: 8a41d7c2e501
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b52e8d3f612"
down_revision: str | None = "8a41d7c2e501"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in (
        "show_dcf_explanation",
        "show_dcf_confidence",
        "show_dcf_scenario_differences",
    ):
        op.add_column(
            "user_settings",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    for name in reversed((
        "show_dcf_explanation",
        "show_dcf_confidence",
        "show_dcf_scenario_differences",
    )):
        op.drop_column("user_settings", name)
