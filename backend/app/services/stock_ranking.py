"""Deterministic ranking over current, official KASE equity rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

SCORE_BY_CATEGORY = {
    "best": "investment",
    "quality": "quality",
    "undervalued": "valuation",
    "growth": "growth",
    "dividends": "dividend",
    "liquid": "liquidity",
    "low_risk": "risk",
    "momentum": "momentum",
}


def _timestamp(item: dict) -> datetime | None:
    raw = item.get("data_timestamp")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def rank_stocks(items: list[dict], category: str, limit: int) -> dict:
    """Exclude obsolete quotes and sort ties by real market activity."""
    official = [
        item for item in items
        if item.get("source") == "kase_public_website"
        and item.get("price") is not None
        and _timestamp(item) is not None
    ]
    latest = max((_timestamp(item) for item in official), default=None)
    cutoff = latest - timedelta(days=7) if latest else None
    eligible = [
        item for item in official
        if cutoff is None or (_timestamp(item) is not None and _timestamp(item) >= cutoff)
    ]

    kind = SCORE_BY_CATEGORY.get(category, "investment")

    def activity(item: dict) -> float:
        return float(item.get("metrics", {}).get("turnover") or 0.0)

    def confidence(item: dict) -> float:
        return float(item.get("scores", {}).get(kind, {}).get("confidence") or 0.0)

    def score(item: dict) -> float | None:
        return item.get("scores", {}).get(kind, {}).get("value")

    def recency(item: dict) -> float:
        value = _timestamp(item)
        return value.timestamp() if value else 0.0

    if kind == "risk":
        eligible.sort(key=lambda item: (
            score(item) is None,
            float(score(item)) if score(item) is not None else 101.0,
            -confidence(item),
            -activity(item),
            -recency(item),
            str(item.get("ticker") or ""),
        ))
    else:
        eligible.sort(key=lambda item: (
            score(item) is not None,
            float(score(item)) if score(item) is not None else -1.0,
            confidence(item),
            activity(item),
            recency(item),
            str(item.get("ticker") or ""),
        ), reverse=True)

    return {
        "items": eligible[:limit],
        "total": len(eligible),
        "limit": limit,
        "category": category,
        "ranking_score": kind,
        "source": "KASE",
        "data_mode": "end_of_day",
        "latest_market_timestamp": latest.isoformat() if latest else None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["SCORE_BY_CATEGORY", "rank_stocks"]
