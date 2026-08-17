"""Mark restated observations instead of overwriting them.

When KASE later publishes a different value for a moment already stored, the
original row is kept and stamped ``superseded_at``; the corrected reading is
inserted alongside it and the change is written to ``historical_corrections``.
Charts and daily aggregates read the current rows only, so the displayed number
is the corrected one while the original stays auditable.

Revision ID: c3d9f5a26e81
Revises: a2b8e4c15d73
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d9f5a26e81"
down_revision: str | None = "a2b8e4c15d73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "market_observations",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_market_observations_superseded_at", "market_observations", ["superseded_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_market_observations_superseded_at", table_name="market_observations")
    op.drop_column("market_observations", "superseded_at")
