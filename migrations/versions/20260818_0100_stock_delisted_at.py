"""Date a delisting instead of only flagging it.

``Instrument.is_active`` already goes false when a share leaves the public
catalogue. This records *when*, from the date KASE stated where it states one.
No history is removed: a delisted share keeps its prices, trades, reports,
dividends and scores (§27).

Revision ID: d4e1a67b3f92
Revises: c3d9f5a26e81
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e1a67b3f92"
down_revision: str | None = "c3d9f5a26e81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stocks", sa.Column("delisted_at", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("stocks", "delisted_at")
