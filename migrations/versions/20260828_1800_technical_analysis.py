"""Technical analysis config version and deterministic cache.

Revision ID: 8a2f19c7d430
Revises: c4d17f0a9e83
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a2f19c7d430"
down_revision: str | None = "c4d17f0a9e83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "technical_indicator_config_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version"),
    )
    op.create_index("ix_technical_indicator_config_versions_version", "technical_indicator_config_versions", ["version"], unique=True)
    op.create_index("ix_technical_indicator_config_versions_activated_at", "technical_indicator_config_versions", ["activated_at"])
    op.create_table(
        "technical_analysis_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("latest_market_observation_id", sa.BigInteger(), nullable=False),
        sa.Column("config_version", sa.String(length=64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("instrument_id", "latest_market_observation_id", "config_version", name="uq_technical_analysis_cache_key"),
    )
    op.create_index("ix_technical_analysis_cache_instrument_id", "technical_analysis_cache", ["instrument_id"])
    op.create_index("ix_technical_analysis_cache_latest_market_observation_id", "technical_analysis_cache", ["latest_market_observation_id"])
    op.create_index("ix_technical_analysis_cache_config_version", "technical_analysis_cache", ["config_version"])
    op.create_index("ix_technical_cache_instrument_created", "technical_analysis_cache", ["instrument_id", "created_at"])


def downgrade() -> None:
    op.drop_table("technical_analysis_cache")
    op.drop_table("technical_indicator_config_versions")
