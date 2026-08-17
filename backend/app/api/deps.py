"""Request-scoped dependencies.

Identity model:

* Anonymous visitors send ``X-Anon-Token`` (a UUID the browser generates once).
  Everything public - TOP, search, bond cards, compare, calculator - works with
  no token at all.
* Registered users are identified by ``X-User-Id``. Full authentication
  (password/OAuth flows, session tokens) is intentionally NOT implemented in
  this stage; this header is the seam it will plug into. Do not deploy the
  write endpoints to the public internet before replacing it.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, ValidationError
from app.db.session import get_session
from app.models.user import User
from app.services.settings_service import SettingsService


@dataclass(slots=True)
class Identity:
    user_id: int | None
    token: str | None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    @property
    def has_owner(self) -> bool:
        return self.user_id is not None or bool(self.token)


def get_identity(
    session: Session = Depends(get_session),
    x_anon_token: str | None = Header(default=None, alias="X-Anon-Token"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> Identity:
    user_id: int | None = None
    if x_user_id:
        if not x_user_id.isdigit():
            raise ValidationError("X-User-Id должен быть числом.")
        user = session.get(User, int(x_user_id))
        if user is None or not user.is_active:
            raise ValidationError("Пользователь не найден.")
        user_id = user.id
    token = (x_anon_token or "").strip() or None
    if token and len(token) > 64:
        raise ValidationError("X-Anon-Token слишком длинный.")
    return Identity(user_id=user_id, token=token)


def require_owner(identity: Identity = Depends(get_identity)) -> Identity:
    """Saving anything needs somebody to save it for."""
    if not identity.has_owner:
        raise ValidationError(
            "Для сохранения нужен идентификатор: заголовок X-Anon-Token "
            "(без регистрации) или X-User-Id (после входа)."
        )
    return identity


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> bool:
    """Guard for operational endpoints.

    In production an unconfigured ``ADMIN_TOKEN`` closes these endpoints
    entirely - the safe direction to fail. Outside production they stay open so
    local work does not need a secret.
    """
    from app.core.config import settings

    expected = settings.ADMIN_TOKEN
    if not expected:
        if settings.is_production:
            raise ForbiddenError(
                "Служебные эндпоинты закрыты: ADMIN_TOKEN не настроен."
            )
        return True
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise ForbiddenError("Требуется корректный заголовок X-Admin-Token.")
    return True


def get_user_settings(
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> dict:
    return SettingsService(session).get(user_id=identity.user_id, token=identity.token)
