"""Leakage-safe event training dataset export."""

from __future__ import annotations

from datetime import timezone
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.news import EventMarketReaction, MarketEvent, NewsArticle


FEATURE_FIELDS = ("event_timestamp", "issuer_id", "instrument_id", "event_type", "sector", "country", "importance", "sentiment", "surprise", "market_regime", "source_confidence", "analysis_confidence")
LABEL_FIELDS = ("return_1d", "return_5d", "return_20d", "abnormal_return_1d", "abnormal_return_5d", "direction_1d", "direction_5d")


def build_training_rows(session: Session) -> list[dict]:
    rows = []
    query = select(MarketEvent, NewsArticle, EventMarketReaction).join(NewsArticle, NewsArticle.id == MarketEvent.news_id).join(EventMarketReaction, EventMarketReaction.event_id == MarketEvent.id)
    for event, article, reaction in session.execute(query):
        # Every feature is known at event_timestamp. Returns are labels only.
        features = {name: getattr(event, name) for name in FEATURE_FIELDS}
        features["event_timestamp"] = event.event_timestamp.isoformat()
        features["text_features"] = {"title": article.title, "summary": article.summary, "language": article.language, "section": article.section}
        labels = {name: getattr(reaction, name) for name in LABEL_FIELDS[:5]}
        labels["direction_1d"] = None if reaction.return_1d is None else int(reaction.return_1d > 0)
        labels["direction_5d"] = None if reaction.return_5d is None else int(reaction.return_5d > 0)
        rows.append({"features": features, "labels": labels})
    return rows


def validate_no_lookahead(rows: list[dict]) -> None:
    forbidden = set(LABEL_FIELDS) | {"future_return", "price_after", "published_later"}
    for index, row in enumerate(rows):
        overlap = forbidden & set(row["features"])
        if overlap: raise ValueError(f"look-ahead fields in row {index}: {sorted(overlap)}")


def export_event_dataset(session: Session, path: str | Path = "datasets/events/event_training_dataset.jsonl") -> dict:
    rows = build_training_rows(session); validate_no_lookahead(rows)
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows), encoding="utf-8")
    manifest = target.with_suffix(".manifest.json")
    manifest.write_text(json.dumps({"format": "jsonl", "rows": len(rows), "features": list(FEATURE_FIELDS) + ["text_features"], "labels": list(LABEL_FIELDS), "look_ahead_validated": True}, indent=2), encoding="utf-8")
    return {"path": str(target), "manifest": str(manifest), "rows": len(rows), "format": "jsonl-equivalent"}


__all__ = ["FEATURE_FIELDS", "LABEL_FIELDS", "build_training_rows", "export_event_dataset", "validate_no_lookahead"]
