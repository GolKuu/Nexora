from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.browser import BrowserNavigationLog, RawBrowserSnapshot


class BrowserSnapshotRepository:
    def __init__(self, session: Session):
        self.session = session

    def store(self, payload: dict) -> RawBrowserSnapshot:
        row = RawBrowserSnapshot(**payload)
        self.session.add(row)
        self.session.flush()
        return row

    def latest_for(self, kind: str, key: str) -> RawBrowserSnapshot | None:
        return self.session.execute(
            select(RawBrowserSnapshot)
            .where(RawBrowserSnapshot.kind == kind, RawBrowserSnapshot.key == key)
            .order_by(desc(RawBrowserSnapshot.fetched_at))
            .limit(1)
        ).scalar_one_or_none()

    def recent(self, *, limit: int = 50, kind: str | None = None) -> list[RawBrowserSnapshot]:
        query = select(RawBrowserSnapshot).order_by(desc(RawBrowserSnapshot.fetched_at)).limit(limit)
        if kind:
            query = query.where(RawBrowserSnapshot.kind == kind)
        return list(self.session.execute(query).scalars())

    def prune(self, *, keep_days: int = 30) -> int:
        """Snapshots are provenance, not an archive: old ones go."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        rows = list(
            self.session.execute(
                select(RawBrowserSnapshot).where(RawBrowserSnapshot.fetched_at < cutoff)
            ).scalars()
        )
        for row in rows:
            self.session.delete(row)
        self.session.flush()
        return len(rows)


class NavigationLogRepository:
    def __init__(self, session: Session):
        self.session = session

    def store_many(self, events: list[dict]) -> int:
        for event in events:
            self.session.add(
                BrowserNavigationLog(
                    session_id=event["session_id"],
                    action_number=event["action_number"],
                    action=event["action"],
                    target=event.get("target"),
                    url_before=event.get("url_before"),
                    url_after=event.get("url_after"),
                    status=event["status"],
                    duration_ms=event.get("duration_ms"),
                    error=event.get("error"),
                )
            )
        self.session.flush()
        return len(events)

    def for_session(self, session_id: str) -> list[BrowserNavigationLog]:
        return list(
            self.session.execute(
                select(BrowserNavigationLog)
                .where(BrowserNavigationLog.session_id == session_id)
                .order_by(BrowserNavigationLog.action_number)
            ).scalars()
        )
