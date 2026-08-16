"""Allow stock and bond alerts for anonymous portfolio owners.

Revision ID: c7d3e42f9066
Revises: b6c2d31e8f55
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "c7d3e42f9066"
down_revision: str | None = "b6c2d31e8f55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("anonymous_token", sa.String(64), nullable=True))
    op.create_index("ix_alerts_anonymous_token", "alerts", ["anonymous_token"])


def downgrade() -> None:
    op.drop_index("ix_alerts_anonymous_token", table_name="alerts")
    with op.batch_alter_table("alerts") as batch:
        batch.drop_column("anonymous_token")
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
