"""Execute selective local-model analyses from normalized change payloads."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.base import ChatMessage
from app.ai.factory import get_llm_client
from app.core.config import settings
from app.models.incremental import AIChangeTask

SYSTEM = (
    "Ты аналитик KASE Bond AI. Объясни только переданные изменения, их влияние "
    "и существенность. Не додумывай отсутствующие факты и не анализируй всю бумагу заново."
)


async def run_ai_change_tasks(
    session: Session, *, since: datetime | None = None, limit: int = 50
) -> dict[str, int]:
    # External providers may be paid and are never invoked by a background job
    # without an explicit user-controlled workflow.
    if not settings.AI_ENABLED or settings.AI_PROVIDER != "local":
        return {"ai_analyses": 0, "ai_failures": 0}
    query = select(AIChangeTask).where(AIChangeTask.status == "pending")
    if since is not None:
        query = query.where(AIChangeTask.created_at >= since)
    rows = session.scalars(query.order_by(AIChangeTask.id).limit(limit)).all()
    client = get_llm_client()
    completed = failed = 0
    for row in rows:
        row.status = "running"
        session.flush()
        response = await client.chat([
            ChatMessage(role="system", content=SYSTEM),
            ChatMessage(role="user", content=json.dumps({
                "task": row.task_type, "ticker": row.ticker, **row.payload_json,
            }, ensure_ascii=False, default=str)),
        ], temperature=0.1, max_tokens=settings.AI_MAX_TOKENS)
        row.finished_at = datetime.now(timezone.utc)
        if response.ok:
            row.status = "completed"
            row.result_json = {"text": response.content, "model": response.model, "provider": response.provider}
            completed += 1
        else:
            row.status = "failed"
            row.error = response.error or "empty model response"
            failed += 1
    session.flush()
    return {"ai_analyses": completed, "ai_failures": failed}


__all__ = ["run_ai_change_tasks"]
