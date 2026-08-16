"""Enforce source-level idempotency for official stock actions.

Revision ID: b6c2d31e8f55
Revises: a5b1c20d7e44
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b6c2d31e8f55"
down_revision: str | None = "a5b1c20d7e44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("corporate_actions") as batch:
        batch.drop_constraint("uq_stock_corporate_action", type_="unique")
        batch.create_unique_constraint("uq_stock_corporate_action_source", ["stock_id", "action_type", "source_url"])
    with op.batch_alter_table("dividends") as batch:
        batch.create_unique_constraint("uq_dividend_source_url", ["stock_id", "source_url"])


def downgrade() -> None:
    with op.batch_alter_table("dividends") as batch:
        batch.drop_constraint("uq_dividend_source_url", type_="unique")
    with op.batch_alter_table("corporate_actions") as batch:
        batch.drop_constraint("uq_stock_corporate_action_source", type_="unique")
        batch.create_unique_constraint("uq_stock_corporate_action", ["stock_id", "action_type", "event_date", "source_url"])
