"""Test configuration.

The suite runs against a throw-away SQLite database created from the same
SQLAlchemy metadata the migrations are generated from, so the schema under test
is the schema that ships.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DB_PATH = ROOT / "tests" / ".test.db"

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
os.environ.setdefault("KASE_DATA_MODE", "mock")
os.environ.setdefault("AI_ENABLED", "false")
os.environ.setdefault("OPENAI_API_KEY", "")


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def engine():
    from app.db.base import Base
    from app.db.session import engine as app_engine
    import app.models  # noqa: F401

    if DB_PATH.exists():
        DB_PATH.unlink()
    Base.metadata.create_all(app_engine)
    yield app_engine
    app_engine.dispose()
    if DB_PATH.exists():
        DB_PATH.unlink(missing_ok=True)


@pytest.fixture
def session(engine):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture(scope="session")
def seeded(engine):
    """A database loaded with the demo dataset, macro data included."""
    from app.collectors.kase_collector import full_sync
    from app.db.session import SessionLocal
    from app.providers.mock_kase import MockKaseProvider

    sys.path.insert(0, str(ROOT / "scripts"))
    from seed_demo import seed_macro  # noqa: E402

    db = SessionLocal()
    try:
        seed_macro(db)
        asyncio.run(full_sync(db, MockKaseProvider()))
    finally:
        db.close()
    return True


@pytest.fixture
def client(seeded):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def api(client):
    """Client bound to the API prefix."""
    from app.core.config import settings

    class Api:
        def __init__(self, c, prefix):
            self._c = c
            self._p = prefix

        def get(self, path, **kw):
            return self._c.get(f"{self._p}{path}", **kw)

        def post(self, path, **kw):
            return self._c.post(f"{self._p}{path}", **kw)

        def put(self, path, **kw):
            return self._c.put(f"{self._p}{path}", **kw)

        def delete(self, path, **kw):
            return self._c.delete(f"{self._p}{path}", **kw)

    return Api(client, settings.API_PREFIX)
