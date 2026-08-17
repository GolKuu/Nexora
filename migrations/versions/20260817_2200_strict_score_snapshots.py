"""Append-only strict score snapshots.

Revision ID: f1c7d3a90b42
Revises: e9f5a64b1288
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1c7d3a90b42"
down_revision: str | None = "e9f5a64b1288"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strict_score_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ticker", sa.String(64), nullable=False),
        sa.Column("bond_id", sa.Integer(), sa.ForeignKey("bonds.id", ondelete="CASCADE")),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE")),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("versions", sa.JSON(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True)),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("facts_fingerprint", sa.String(64), nullable=False),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("base_score", sa.Float(), nullable=False),
        sa.Column("penalised_score", sa.Float(), nullable=False),
        sa.Column("data_quality", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("breakdown", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "ticker", "kind", "model_version", "as_of", "facts_fingerprint",
            name="uq_strict_score_snapshot",
        ),
    )
    for name, cols in (
        ("ix_strict_score_snapshots_kind", ["kind"]),
        ("ix_strict_score_snapshots_ticker", ["ticker"]),
        ("ix_strict_score_snapshots_bond_id", ["bond_id"]),
        ("ix_strict_score_snapshots_stock_id", ["stock_id"]),
        ("ix_strict_score_snapshots_model_version", ["model_version"]),
        ("ix_strict_score_snapshots_as_of", ["as_of"]),
        ("ix_strict_score_snapshots_calculated_at", ["calculated_at"]),
        ("ix_strict_score_snapshots_facts_fingerprint", ["facts_fingerprint"]),
        ("ix_strict_scores_ticker_calculated", ["ticker", "calculated_at"]),
        ("ix_strict_scores_kind_score", ["kind", "final_score"]),
    ):
        op.create_index(name, "strict_score_snapshots", cols)


def downgrade() -> None:
    op.drop_table("strict_score_snapshots")
