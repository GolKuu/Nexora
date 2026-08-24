"""Add deterministic DCF valuation audit domain.

Revision ID: 8a41d7c2e501
Revises: f6a3c92d8b14
"""
from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "8a41d7c2e501"
down_revision: str | None = "f6a3c92d8b14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The canonical metadata is intentionally used here: all DCF tables are a
    # cohesive new domain and portable across PostgreSQL/SQLite.
    from app.db.base import Base
    import app.models  # noqa: F401
    bind = op.get_bind()
    for table_name in ("dcf_subscriptions", "dcf_runs", "dcf_scenario_results",
        "dcf_input_snapshots", "dcf_assumptions", "dcf_validation_results", "dcf_usage_events",
        "dcf_cost_events", "disclaimer_configs", "dcf_model_versions"):
        Base.metadata.tables[table_name].create(bind, checkfirst=True)


def downgrade() -> None:
    for table_name in ("dcf_model_versions", "disclaimer_configs", "dcf_cost_events",
        "dcf_usage_events", "dcf_validation_results", "dcf_assumptions", "dcf_input_snapshots",
        "dcf_scenario_results", "dcf_runs", "dcf_subscriptions"):
        op.drop_table(table_name)
