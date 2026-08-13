"""financial statements: total_liabilities

KASE's public ``/api/companies/fin-data/{code}/`` feed reports total
liabilities but no borrowings breakdown, so leverage has to be measured from
this line. It is deliberately a separate column from ``total_debt``: total
liabilities include payables and, for banks, customer deposits, and treating
the two as interchangeable would overstate borrowings.

Revision ID: c2a7e3b41f58
Revises: b1f4c7a90d21
Create Date: 2026-08-13 03:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c2a7e3b41f58'
down_revision: str | None = 'b1f4c7a90d21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financial_statements",
        sa.Column("total_liabilities", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("financial_statements", "total_liabilities")
