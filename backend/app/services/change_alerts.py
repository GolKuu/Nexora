"""Evaluate user thresholds from field-level changes, never from a full rescan."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incremental import DataChangeSet
from app.models.portfolio import Alert


class ChangeAlertEngine:
    FIELDS = {
        "ytm_above": ("ytm", "above"), "ytm_below": ("ytm", "below"),
        "price_above": ({"last", "price", "close"}, "above"), "price_below": ({"last", "price", "close"}, "below"),
        "score_above": ({"investment_score", "investment"}, "above"),
        "pe_below": ({"pe"}, "below"),
    }
    EVENTS = {
        "dividend_announced": "dividends", "financial_report": "financials",
        "company_news": "news", "score_change": "scores",
    }

    def evaluate_since(self, since: datetime) -> int:
        changes = self.session.scalars(select(DataChangeSet).where(
            DataChangeSet.detected_at >= since,
            DataChangeSet.change_type == "updated",
        )).all()
        triggered = 0
        for change in changes:
            try:
                entity_id = int(change.entity_id)
            except (TypeError, ValueError):
                continue
            identity_filter = Alert.stock_id == entity_id if change.entity_type == "stock" else Alert.bond_id == entity_id
            alerts = self.session.scalars(select(Alert).where(identity_filter, Alert.is_active.is_(True))).all()
            for alert in alerts:
                event_section = self.EVENTS.get(alert.kind)
                if event_section and change.section == event_section:
                    if alert.kind == "dividend_announced" and "announced" not in str(change.new_value).casefold():
                        continue
                    alert.last_triggered_at = datetime.now(timezone.utc)
                    alert.message = f"Новое изменение в разделе {change.section}: {change.field}"
                    triggered += 1
                    continue
                if alert.kind == "profit_change" and change.material and change.field.rsplit(".", 1)[-1] in {"net_income", "earnings_growth"}:
                    alert.last_triggered_at = datetime.now(timezone.utc)
                    alert.message = f"Изменилась прибыль: {change.old_value} → {change.new_value}"
                    triggered += 1
                    continue
                spec = self.FIELDS.get(alert.kind)
                if not spec or change.field.rsplit(".", 1)[-1] not in (spec[0] if isinstance(spec[0], set) else {spec[0]}) or alert.threshold is None:
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
