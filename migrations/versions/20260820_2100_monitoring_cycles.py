"""Record every monitoring cycle so its health can be answered from evidence.

The ten-minute loop already ran server-side, but the only trace it left was a
log line: a restart threw it away and the operational endpoints could not read
it. ``monitoring_cycles`` is append-only telemetry about the *run* - how many
instruments were checked, how many actually changed, how long it took and what
failed. It holds no market fact and is never a source for one (§41).

Revision ID: e5f2b78c4a03
Revises: d4e1a67b3f92
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f2b78c4a03"
down_revision: str | None = "d4e1a67b3f92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: list[tuple[str, list[str]]] = [
    ("ix_monitoring_cycles_job_type", ["job_type"]),
    ("ix_monitoring_cycles_started_at", ["started_at"]),
    ("ix_monitoring_cycles_finished_at", ["finished_at"]),
    ("ix_monitoring_cycles_status", ["status"]),
    ("ix_monitoring_cycles_job_started", ["job_type", "started_at"]),
    ("ix_monitoring_cycles_status_started", ["status", "started_at"]),
]


def upgrade() -> None:
    op.create_table(
        "monitoring_cycles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("instruments_checked", sa.Integer(), nullable=False),
        sa.Column("instruments_changed", sa.Integer(), nullable=False),
        sa.Column("observations_created", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("anomalies", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("market_day", sa.Boolean(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("detail", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns in _INDEXES:
        op.create_index(name, "monitoring_cycles", columns)


def downgrade() -> None:
    for name, _ in reversed(_INDEXES):
        op.drop_index(name, table_name="monitoring_cycles")
    op.drop_table("monitoring_cycles")
