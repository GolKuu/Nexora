"""Probabilistic stock forecasts, registry and out-of-sample evaluations.

Revision ID: e9f5a64b1288
Revises: d8e4f53a0177
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "e9f5a64b1288"
down_revision: str | None = "d8e4f53a0177"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "forecast_model_versions", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id", ondelete="CASCADE")),
        sa.Column("model_version", sa.String(64), nullable=False, unique=True), sa.Column("market", sa.String(32), nullable=False),
        sa.Column("training_period_start", sa.DateTime(timezone=True)), sa.Column("training_period_end", sa.DateTime(timezone=True)),
        sa.Column("training_dataset_version", sa.String(64), nullable=False), sa.Column("features_version", sa.String(32), nullable=False),
        sa.Column("hyperparameters", sa.JSON(), nullable=False), sa.Column("evaluation_metrics", sa.JSON(), nullable=False),
        sa.Column("production_status", sa.String(24), nullable=False), *_timestamps(),
    )
    op.create_index("ix_forecast_model_versions_model_version", "forecast_model_versions", ["model_version"])
    op.create_index("ix_forecast_model_versions_instrument_id", "forecast_model_versions", ["instrument_id"])
    op.create_index("ix_forecast_models_market_status", "forecast_model_versions", ["market", "production_status"])
    op.create_table(
        "forecast_snapshots", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False), sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_market_time", sa.DateTime(timezone=True), nullable=False), sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("data_mode", sa.String(24), nullable=False), sa.Column("features_hash", sa.String(64), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False), sa.Column("current_price", sa.Float(), nullable=False),
        sa.Column("prediction", sa.JSON(), nullable=False), sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False), sa.Column("event_id", sa.Integer(), sa.ForeignKey("market_events.id", ondelete="SET NULL")),
        *_timestamps(), sa.UniqueConstraint("instrument_id", "model_version", "generated_at", "horizon", name="uq_forecast_snapshot"),
    )
    for name, cols in (("ix_forecast_snapshots_instrument_id", ["instrument_id"]), ("ix_forecast_snapshots_model_version", ["model_version"]),
                       ("ix_forecast_snapshots_generated_at", ["generated_at"]), ("ix_forecast_snapshots_as_of_market_time", ["as_of_market_time"]),
                       ("ix_forecast_snapshots_horizon", ["horizon"]), ("ix_forecast_snapshots_event_id", ["event_id"]),
                       ("ix_forecast_snapshots_instrument_generated", ["instrument_id", "generated_at"])):
        op.create_index(name, "forecast_snapshots", cols)
    op.create_table(
        "forecast_evaluations", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("forecast_snapshots.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("realized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("realized_price", sa.Float(), nullable=False), sa.Column("realized_return", sa.Float(), nullable=False),
        sa.Column("direction_correct", sa.Boolean(), nullable=False), sa.Column("interval_50_hit", sa.Boolean(), nullable=False),
        sa.Column("interval_80_hit", sa.Boolean(), nullable=False), sa.Column("brier_score", sa.Float(), nullable=False),
        sa.Column("absolute_error", sa.Float(), nullable=False), *_timestamps(),
    )
    op.create_index("ix_forecast_evaluations_snapshot_id", "forecast_evaluations", ["snapshot_id"])
    op.create_table(
        "forecast_changes", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("previous_snapshot_id", sa.Integer(), sa.ForeignKey("forecast_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("current_snapshot_id", sa.Integer(), sa.ForeignKey("forecast_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False), sa.Column("probability_change", sa.Float(), nullable=False),
        sa.Column("expected_return_change", sa.Float(), nullable=False), sa.Column("interval_width_change", sa.Float(), nullable=False),
        sa.Column("confidence_change", sa.Float(), nullable=False), sa.Column("reason", sa.Text(), nullable=False), *_timestamps(),
    )
    op.create_index("ix_forecast_changes_instrument_id", "forecast_changes", ["instrument_id"])
    op.create_index("ix_forecast_changes_instrument_created", "forecast_changes", ["instrument_id", "created_at"])


def downgrade() -> None:
    for table in ("forecast_changes", "forecast_evaluations", "forecast_snapshots", "forecast_model_versions"):
        op.drop_table(table)
