"""Evaluate user thresholds from field-level changes, never from a full rescan."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incremental import DataChangeSet
from app.models.portfolio import Alert


class ChangeAlertEngine:
    FIELDS = {
        "ytm_above": ("ytm", "above"), "ytm_below": ("ytm", "below"),
        "price_above": ("last", "above"), "price_below": ("last", "below"),
        "score_above": ("investment_score", "above"),
    }

    def evaluate_since(self, since: datetime) -> int:
        changes = self.session.scalars(select(DataChangeSet).where(
            DataChangeSet.detected_at >= since,
            DataChangeSet.change_type == "updated",
        )).all()
        triggered = 0
        for change in changes:
            try:
                bond_id = int(change.entity_id)
            except (TypeError, ValueError):
                continue
            alerts = self.session.scalars(select(Alert).where(
                Alert.bond_id == bond_id, Alert.is_active.is_(True)
            )).all()
            for alert in alerts:
                spec = self.FIELDS.get(alert.kind)
                if not spec or change.field.rsplit(".", 1)[-1] != spec[0] or alert.threshold is None:
                    continue
                try:
                    old, new = float(change.old_value), float(change.new_value)
                except (TypeError, ValueError):
                    continue
                threshold = float(alert.threshold)
                crossed = (old <= threshold < new) if spec[1] == "above" else (old >= threshold > new)
                if crossed:
                    alert.last_triggered_at = datetime.now(timezone.utc)
                    alert.message = f"{change.field}: {old:g} → {new:g} (порог {threshold:g})"
                    triggered += 1
        self.session.flush()
        return triggered

    def __init__(self, session: Session):
        self.session = session


__all__ = ["ChangeAlertEngine"]
