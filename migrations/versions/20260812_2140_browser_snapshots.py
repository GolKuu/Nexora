"""browser agent: raw snapshots and navigation log

Revision ID: b1f4c7a90d21
Revises: e0021e518807
Create Date: 2026-08-12 21:40:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b1f4c7a90d21'
down_revision: str | None = 'e0021e518807'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'raw_browser_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(length=1024), nullable=False),
        sa.Column('page_title', sa.String(length=512), nullable=True),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('key', sa.String(length=128), nullable=True),
        sa.Column('section', sa.String(length=128), nullable=True),
        sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('html_hash', sa.String(length=64), nullable=True),
        sa.Column('visible_text', sa.Text(), nullable=True),
        sa.Column('extracted_json', sa.JSON(), nullable=True),
        sa.Column('screenshot_path', sa.String(length=1024), nullable=True),
        sa.Column('browser_version', sa.String(length=64), nullable=True),
        sa.Column('extractor_version', sa.String(length=32), nullable=True),
        sa.Column('browser_session_id', sa.String(length=32), nullable=True),
        sa.Column('language', sa.String(length=8), nullable=True),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('blocked_by_captcha', sa.Boolean(), nullable=False),
        sa.Column('requires_authentication', sa.Boolean(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_raw_browser_snapshots')),
    )
    with op.batch_alter_table('raw_browser_snapshots', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_raw_browser_snapshots_url'), ['url'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_raw_browser_snapshots_kind'), ['kind'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_raw_browser_snapshots_fetched_at'), ['fetched_at'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_raw_browser_snapshots_html_hash'), ['html_hash'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_raw_browser_snapshots_browser_session_id'),
            ['browser_session_id'],
            unique=False,
        )
        batch_op.create_index(
            'ix_browser_snapshot_kind_key', ['kind', 'key', 'fetched_at'], unique=False
        )

    op.create_table(
        'browser_navigation_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=32), nullable=False),
        sa.Column('action_number', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target', sa.String(length=512), nullable=True),
        sa.Column('url_before', sa.String(length=1024), nullable=True),
        sa.Column('url_after', sa.String(length=1024), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('duration_ms', sa.Float(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_browser_navigation_log')),
    )
    with op.batch_alter_table('browser_navigation_log', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_browser_navigation_log_session_id'), ['session_id'], unique=False
        )
        batch_op.create_index(
            'ix_browser_nav_session_action', ['session_id', 'action_number'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('browser_navigation_log', schema=None) as batch_op:
        batch_op.drop_index('ix_browser_nav_session_action')
        batch_op.drop_index(batch_op.f('ix_browser_navigation_log_session_id'))
    op.drop_table('browser_navigation_log')

    with op.batch_alter_table('raw_browser_snapshots', schema=None) as batch_op:
        batch_op.drop_index('ix_browser_snapshot_kind_key')
        batch_op.drop_index(batch_op.f('ix_raw_browser_snapshots_browser_session_id'))
        batch_op.drop_index(batch_op.f('ix_raw_browser_snapshots_html_hash'))
        batch_op.drop_index(batch_op.f('ix_raw_browser_snapshots_fetched_at'))
        batch_op.drop_index(batch_op.f('ix_raw_browser_snapshots_kind'))
        batch_op.drop_index(batch_op.f('ix_raw_browser_snapshots_url'))
    op.drop_table('raw_browser_snapshots')
