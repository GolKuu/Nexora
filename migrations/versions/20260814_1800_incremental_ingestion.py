"""incremental KASE ingestion state, history and work queues

Revision ID: d4c8a10f6e21
Revises: c2a7e3b41f58
Create Date: 2026-08-14 18:00:00+00:00
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "d4c8a10f6e21"
down_revision: str | None = "c2a7e3b41f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _index(table: str, name: str, columns: list[str], unique: bool = False) -> None:
    op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    op.add_column("bonds", sa.Column("last_checked_at", sa.DateTime(timezone=True)))
    op.add_column("bonds", sa.Column("last_changed_at", sa.DateTime(timezone=True)))
    _index("bonds", "ix_bonds_last_checked_at", ["last_checked_at"])
    _index("bonds", "ix_bonds_last_changed_at", ["last_changed_at"])
    op.add_column("bond_trades", sa.Column("fingerprint", sa.String(64)))
    _index("bond_trades", "ix_bond_trades_fingerprint", ["fingerprint"])
    with op.batch_alter_table("bond_trades") as batch_op:
        batch_op.create_unique_constraint("uq_trade_fingerprint", ["fingerprint"])

    source = [
        sa.Column("source", sa.String(64)), sa.Column("source_identifier", sa.String(255)),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
    ]
    quote_fields = [
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(n, sa.Float()) for n in ("bid", "ask", "bid_volume", "ask_volume", "last", "clean_price", "dirty_price", "accrued_interest", "ytm", "volume", "turnover")],
        sa.Column("number_of_trades", sa.Integer()), sa.Column("data_mode", sa.String(16), nullable=False),
    ]
    op.create_table(
        "bond_quote_current", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bond_id", sa.Integer(), sa.ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False),
        *quote_fields, *source,
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False), *_timestamps(),
        sa.UniqueConstraint("bond_id", name="uq_quote_current_bond"),
    )
    for name, cols in (("bond_id", ["bond_id"]), ("last_checked_at", ["last_checked_at"]), ("last_changed_at", ["last_changed_at"]), ("content_hash", ["content_hash"])):
        _index("bond_quote_current", f"ix_bond_quote_current_{name}", cols)

    op.create_table(
        "data_current_state", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(32), nullable=False), sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("ticker", sa.String(64)), sa.Column("isin", sa.String(12)), sa.Column("section", sa.String(64), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=False), sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("etag", sa.String(255)), sa.Column("last_modified", sa.String(255)), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("normalized_json", sa.JSON(), nullable=False), sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("raw_browser_snapshots.id", ondelete="SET NULL")), *_timestamps(),
        sa.UniqueConstraint("entity_type", "entity_id", "section", name="uq_current_entity_section"),
    )
    for name in ("entity_type", "entity_id", "ticker", "isin", "section", "source_url", "content_hash"):
        _index("data_current_state", f"ix_data_current_state_{name}", [name])
    for name, cols in (("entity_section", ["entity_id", "section"]), ("last_checked", ["last_checked_at"]), ("last_changed", ["last_changed_at"]), ("source_timestamp", ["source_timestamp"]), ("content_hash", ["content_hash"])):
        _index("data_current_state", f"ix_current_{name}", cols)

    op.create_table(
        "data_state_versions", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("current_state_id", sa.Integer(), sa.ForeignKey("data_current_state.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False), sa.Column("entity_id", sa.String(128), nullable=False), sa.Column("section", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("normalized_json", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False), sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("raw_browser_snapshots.id", ondelete="SET NULL")), sa.Column("parser_version", sa.String(32), nullable=False), *_timestamps(),
        sa.UniqueConstraint("current_state_id", "content_hash", name="uq_state_version_hash"),
    )
    for name in ("current_state_id", "entity_id", "section", "detected_at"):
        _index("data_state_versions", f"ix_data_state_versions_{name}", [name])
    _index("data_state_versions", "ix_state_version_hash", ["content_hash"])
    _index("data_state_versions", "ix_state_version_entity_section", ["entity_id", "section", "detected_at"])

    op.create_table(
        "ingestion_jobs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("job_type", sa.String(32), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False), *[sa.Column(n, sa.Integer(), nullable=False) for n in ("entities_checked", "entities_changed", "entities_unchanged", "entities_failed", "new_records", "updated_records", "ai_tasks_created")],
        sa.Column("metrics_json", sa.JSON()), sa.Column("error_summary", sa.Text()),
    )
    _index("ingestion_jobs", "ix_ingestion_jobs_idempotency_key", ["idempotency_key"], unique=True)
    for name in ("job_type", "started_at", "status"):
        _index("ingestion_jobs", f"ix_ingestion_jobs_{name}", [name])
    _index("ingestion_jobs", "ix_ingestion_job_type_started", ["job_type", "started_at"])

    op.create_table(
        "data_change_sets", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False), sa.Column("ticker", sa.String(64)), sa.Column("isin", sa.String(12)),
        sa.Column("section", sa.String(64), nullable=False), sa.Column("field", sa.String(128), nullable=False), sa.Column("old_value", sa.JSON()), sa.Column("new_value", sa.JSON()),
        sa.Column("change_type", sa.String(16), nullable=False), sa.Column("source_url", sa.String(1024), nullable=False), sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False), sa.Column("snapshot_before_id", sa.Integer(), sa.ForeignKey("data_state_versions.id", ondelete="SET NULL")),
        sa.Column("snapshot_after_id", sa.Integer(), sa.ForeignKey("data_state_versions.id", ondelete="SET NULL")), sa.Column("parser_version", sa.String(32), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False), sa.Column("material", sa.Boolean(), nullable=False), sa.Column("suspected_anomaly", sa.Boolean(), nullable=False),
        sa.Column("change_fingerprint", sa.String(64), nullable=False), *_timestamps(),
        sa.UniqueConstraint("entity_type", "entity_id", "section", "field", "snapshot_after_id", "change_fingerprint", name="uq_change_event"),
    )
    for name in ("entity_type", "entity_id", "ticker", "isin", "section", "field", "detected_at", "material"):
        _index("data_change_sets", f"ix_data_change_sets_{name}", [name])
    for name, cols in (("entity_section", ["entity_id", "section", "detected_at"]), ("source_url", ["source_url"]), ("source_timestamp", ["source_timestamp"]), ("type", ["change_type"]), ("importance", ["importance"])):
        _index("data_change_sets", f"ix_changes_{name}", cols)

    op.create_table(
        "source_check_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source_url", sa.String(1024), nullable=False),
        sa.Column("entity_id", sa.String(128)), sa.Column("section", sa.String(64)), sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("http_status", sa.Integer()), sa.Column("latency_ms", sa.Float()), sa.Column("etag", sa.String(255)), sa.Column("last_modified", sa.String(255)),
        sa.Column("content_hash", sa.String(64)), sa.Column("changed", sa.Boolean(), nullable=False), sa.Column("error", sa.Text()),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("ingestion_jobs.id", ondelete="SET NULL")),
    )
    for name in ("source_url", "entity_id", "checked_at", "status"):
        _index("source_check_logs", f"ix_source_check_logs_{name}", [name])
    for name, cols in (("source_checked", ["source_url", "checked_at"]), ("entity_checked", ["entity_id", "checked_at"]), ("content_hash", ["content_hash"])):
        _index("source_check_logs", f"ix_check_{name}", cols)

    _create_work_and_content_tables()


def _create_work_and_content_tables() -> None:
    op.create_table("recalculation_tasks", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entity_type", sa.String(32), nullable=False), sa.Column("entity_id", sa.String(128), nullable=False), sa.Column("task_type", sa.String(64), nullable=False), sa.Column("reason", sa.String(128), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("priority", sa.Integer(), nullable=False), sa.Column("dedupe_key", sa.String(64), nullable=False), *_timestamps(), sa.UniqueConstraint("dedupe_key", name="uq_recalculation_dedupe"))
    for name in ("entity_id", "task_type"): _index("recalculation_tasks", f"ix_recalculation_tasks_{name}", [name])
    _index("recalculation_tasks", "ix_recalc_status_priority", ["status", "priority"])
    op.create_table("ai_change_tasks", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entity_type", sa.String(32), nullable=False), sa.Column("entity_id", sa.String(128), nullable=False), sa.Column("ticker", sa.String(64)), sa.Column("task_type", sa.String(64), nullable=False), sa.Column("payload_json", sa.JSON(), nullable=False), sa.Column("model_version", sa.String(64)), sa.Column("status", sa.String(16), nullable=False), sa.Column("dedupe_key", sa.String(64), nullable=False), sa.Column("result_json", sa.JSON()), sa.Column("error", sa.Text()), sa.Column("finished_at", sa.DateTime(timezone=True)), *_timestamps(), sa.UniqueConstraint("dedupe_key", name="uq_ai_change_task_dedupe"))
    for name in ("entity_id", "ticker", "task_type"): _index("ai_change_tasks", f"ix_ai_change_tasks_{name}", [name])
    _index("ai_change_tasks", "ix_ai_change_status", ["status", "task_type"])
    op.create_table("kase_documents", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entity_id", sa.String(128), nullable=False), sa.Column("ticker", sa.String(64)), sa.Column("issuer_code", sa.String(64)), sa.Column("document_url", sa.String(1024), nullable=False), sa.Column("document_name", sa.String(512), nullable=False), sa.Column("document_type", sa.String(64)), sa.Column("publication_date", sa.DateTime(timezone=True)), sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("current_version_id", sa.Integer(), sa.ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True, name="fk_kase_documents_current_version_id_document_versions")), *_timestamps(), sa.UniqueConstraint("document_url", name="uq_document_url"))
    for name in ("entity_id", "ticker", "issuer_code", "document_url", "last_checked_at", "last_changed_at"): _index("kase_documents", f"ix_kase_documents_{name}", [name])
    op.create_table("document_versions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("document_id", sa.Integer(), sa.ForeignKey("kase_documents.id", ondelete="CASCADE"), nullable=False), sa.Column("version_number", sa.Integer(), nullable=False), sa.Column("file_size", sa.Integer()), sa.Column("etag", sa.String(255)), sa.Column("last_modified", sa.String(255)), sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False), sa.Column("storage_path", sa.String(1024)), sa.Column("analysis_status", sa.String(16), nullable=False), *_timestamps(), sa.UniqueConstraint("document_id", "content_hash", name="uq_document_version_hash"))
    _index("document_versions", "ix_document_versions_document_id", ["document_id"]); _index("document_versions", "ix_document_version_hash", ["content_hash"])
    op.create_table("kase_news_items", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("entity_id", sa.String(128), nullable=False), sa.Column("ticker", sa.String(64)), sa.Column("issuer_code", sa.String(64)), sa.Column("stable_identifier", sa.String(128)), sa.Column("fingerprint", sa.String(64), nullable=False), sa.Column("title", sa.String(1024), nullable=False), sa.Column("publication_date", sa.DateTime(timezone=True)), sa.Column("url", sa.String(1024), nullable=False), sa.Column("content_hash", sa.String(64)), sa.Column("analyzed_at", sa.DateTime(timezone=True)), *_timestamps(), sa.UniqueConstraint("fingerprint", name="uq_news_fingerprint"))
    for name in ("entity_id", "ticker", "issuer_code", "stable_identifier"): _index("kase_news_items", f"ix_kase_news_items_{name}", [name])
    _index("kase_news_items", "ix_news_entity_published", ["entity_id", "publication_date"])


def downgrade() -> None:
    for table in ("kase_news_items", "document_versions", "kase_documents", "ai_change_tasks", "recalculation_tasks", "source_check_logs", "data_change_sets", "ingestion_jobs", "data_state_versions", "data_current_state", "bond_quote_current"):
        op.drop_table(table)
    with op.batch_alter_table("bond_trades") as batch_op:
        batch_op.drop_constraint("uq_trade_fingerprint", type_="unique")
    op.drop_index("ix_bond_trades_fingerprint", table_name="bond_trades")
    op.drop_column("bond_trades", "fingerprint")
    op.drop_index("ix_bonds_last_changed_at", table_name="bonds")
    op.drop_index("ix_bonds_last_checked_at", table_name="bonds")
    op.drop_column("bonds", "last_changed_at")
    op.drop_column("bonds", "last_checked_at")
