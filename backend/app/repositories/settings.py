from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import UserSettings


class SettingsRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, *, user_id: int | None, token: str | None) -> UserSettings | None:
        if user_id is not None:
            return self.session.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            ).scalar_one_or_none()
        if token:
            return self.session.execute(
                select(UserSettings).where(UserSettings.anonymous_token == token)
            ).scalar_one_or_none()
        return None

    def get_or_create(self, *, user_id: int | None, token: str | None) -> UserSettings:
        existing = self.get(user_id=user_id, token=token)
        if existing is not None:
            return existing
        created = UserSettings(user_id=user_id, anonymous_token=token)
        self.session.add(created)
        self.session.flush()
        return created

    def update(self, settings_row: UserSettings, values: dict) -> UserSettings:
        for key, value in values.items():
            if hasattr(settings_row, key):
                setattr(settings_row, key, value)
        self.session.flush()
        return settings_row
