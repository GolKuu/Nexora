"""Investment goals, immutable plan versions, and planned portfolio positions.

Revision ID: 1f7a0b9c2d41
Revises: 8a2f19c7d430
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1f7a0b9c2d41"
down_revision: str | None = "8a2f19c7d430"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "investment_goals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("anonymous_token", sa.String(64), nullable=True),
        sa.Column("starting_capital", sa.Float(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_amount", sa.Float(), nullable=False),
        sa.Column("target_final_value", sa.Float(), nullable=False),
        sa.Column("horizon_months", sa.Integer(), nullable=False),
        sa.Column("monthly_contribution", sa.Float(), nullable=False, server_default="0"),
        sa.Column("risk_profile", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="KZT"),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("user_id", "anonymous_token", "status"):
        op.create_index(f"ix_investment_goals_{column}", "investment_goals", [column])
    op.create_table(
        "goal_plan_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("goal_id", sa.Integer(), sa.ForeignKey("investment_goals.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("methodology_version", sa.String(32), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("goal_id", "version", name="uq_goal_plan_version"),
    )
    op.create_index("ix_goal_plan_versions_goal_id", "goal_plan_versions", ["goal_id"])
    with op.batch_alter_table("portfolios") as batch:
        batch.add_column(sa.Column("goal_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_portfolios_goal_id_investment_goals", "investment_goals", ["goal_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_portfolios_goal_id", ["goal_id"])
    with op.batch_alter_table("portfolio_positions") as batch:
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="EXECUTED"))
        batch.add_column(sa.Column("goal_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("planned_quantity", sa.Float(), nullable=True))
        batch.add_column(sa.Column("planned_reference_price", sa.Float(), nullable=True))
        batch.add_column(sa.Column("planned_allocation", sa.Float(), nullable=True))
        batch.add_column(sa.Column("actual_quantity", sa.Float(), nullable=True))
        batch.add_column(sa.Column("actual_price", sa.Float(), nullable=True))
        batch.add_column(sa.Column("actual_commission", sa.Float(), nullable=True))
        batch.add_column(sa.Column("execution_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("source_goal_plan_version_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_portfolio_positions_goal_id_investment_goals", "investment_goals", ["goal_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_portfolio_positions_source_goal_plan_version_id_goal_plan_versions", "goal_plan_versions", ["source_goal_plan_version_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_portfolio_positions_status", ["status"])
        batch.create_index("ix_portfolio_positions_goal_id", ["goal_id"])
        batch.create_index("ix_portfolio_positions_source_goal_plan_version_id", ["source_goal_plan_version_id"])


def downgrade() -> None:
    with op.batch_alter_table("portfolio_positions") as batch:
        for index in ("ix_portfolio_positions_source_goal_plan_version_id", "ix_portfolio_positions_goal_id", "ix_portfolio_positions_status"):
            batch.drop_index(index)
        for column in ("source_goal_plan_version_id", "execution_date", "actual_commission", "actual_price", "actual_quantity", "planned_allocation", "planned_reference_price", "planned_quantity", "goal_id", "status"):
            batch.drop_column(column)
    with op.batch_alter_table("portfolios") as batch:
        batch.drop_index("ix_portfolios_goal_id")
        batch.drop_column("goal_id")
    op.drop_table("goal_plan_versions")
    op.drop_table("investment_goals")

