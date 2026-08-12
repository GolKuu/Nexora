from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import DataSource, RawKaseData


class DataSourceRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(self, code: str, **values) -> DataSource:
        source = self.session.execute(
            select(DataSource).where(DataSource.code == code)
        ).scalar_one_or_none()
        if source is None:
            source = DataSource(code=code, name=values.pop("name", code), **values)
            self.session.add(source)
            self.session.flush()
        return source

    def record_success(self, code: str) -> None:
        source = self.get_or_create(code, kind="unknown")
        source.last_success_at = datetime.now(timezone.utc)
        source.last_error = None
        self.session.flush()

    def record_failure(self, code: str, error: str) -> None:
        source = self.get_or_create(code, kind="unknown")
        source.last_failure_at = datetime.now(timezone.utc)
        source.last_error = error[:2000]
        self.session.flush()

    def list(self) -> list[DataSource]:
        return list(self.session.execute(select(DataSource)).scalars())


class RawDataRepository:
    def __init__(self, session: Session):
        self.session = session

    def store(self, payload: dict) -> RawKaseData:
        row = RawKaseData(**payload)
        self.session.add(row)
        self.session.flush()
        return row

    def store_many(self, payloads: list[dict]) -> int:
        for payload in payloads:
            self.session.add(RawKaseData(**payload))
        self.session.flush()
        return len(payloads)
