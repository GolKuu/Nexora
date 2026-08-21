"""Alembic environment.

The schema is defined once, in the SQLAlchemy models, and only portable column
types are used - so the same migrations run on PostgreSQL in production and on
SQLite in the test-suite.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base  # noqa: E402
import app.models  # noqa: F401,E402  (registers every table)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The process environment wins, then the app's own settings - which read the
# same .env the backend reads. Without that fallback `alembic upgrade head`
# would fail on a checkout whose DATABASE_URL lives only in .env, even though
# the application itself starts fine.
database_url = os.getenv("DATABASE_URL")
if not database_url:
    try:
        from app.core.config import settings  # noqa: E402

        database_url = settings.DATABASE_URL
    except Exception:  # pragma: no cover - settings are optional for offline use
        database_url = None
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)
elif not config.get_main_option("sqlalchemy.url", None):
    raise RuntimeError(
        "DATABASE_URL is not set, .env has none, and alembic.ini has no sqlalchemy.url."
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Batch mode keeps ALTERs working on SQLite too.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
