"""Drop the DCF explanation preference.

The valuation's assumptions are no longer disclosed to the client at all, so a
switch that used to reveal them has nothing left to control. The explanation
data itself is untouched: it stays on the run and is served by the audit
endpoint, which is where model review reads it.

Revision ID: c4d17f0a9e83
Revises: 9b52e8d3f612
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d17f0a9e83"
down_revision: str | None = "9b52e8d3f612"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("user_settings", "show_dcf_explanation")


def downgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("show_dcf_explanation", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
