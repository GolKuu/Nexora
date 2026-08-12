"""AI explanations, always with a deterministic fallback.

The explanation the user sees is generated from the score components by
``app.scoring.explain``. The LLM only rewrites that text more fluently; if it
is unavailable, slow or misconfigured, the deterministic text is shown instead
and the response says which one it is.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.ai.base import ChatMessage, LLMClient
from app.ai.factory import get_llm_client
from app.ai.prompts import PROMPT_VERSION, SYSTEM_EXPLAINER, explain_score_prompt
from app.core.logging import get_logger
from app.models.ai import AIAnalysis

logger = get_logger(__name__)

CACHE_TTL = timedelta(hours=12)


def deterministic_text(explanation: dict) -> str:
    """Plain-Python explanation. This is the guaranteed answer."""
    value = explanation.get("value")
    if value is None:
        return (
            "Общую оценку посчитать не удалось: не хватает данных по этому выпуску. "
            "Показаны только те показатели, которые известны."
        )
    parts = [
        f"Общая оценка {value:.0f} из 100 — {explanation.get('verdict')}. "
        f"{explanation.get('summary', '')}".strip()
    ]
    strengths = explanation.get("strengths") or []
    if strengths:
        names = ", ".join(s["label"].lower() for s in strengths)
        parts.append(f"Сильные стороны: {names}.")
    weaknesses = explanation.get("weaknesses") or []
    if weaknesses:
        names = ", ".join(w["label"].lower() for w in weaknesses)
        parts.append(f"Слабые стороны: {names}.")
    missing = explanation.get("missing_data") or []
    if missing:
        names = ", ".join(m["label"].lower() for m in missing)
        parts.append(f"Нет данных по: {names} — оценка построена без них.")
    notes = explanation.get("notes")
    if notes:
        parts.append(notes)
    return " ".join(parts)


class ExplainerService:
    def __init__(self, session: Session, client: LLMClient | None = None):
        self.session = session
        self.client = client or get_llm_client()

    def _cached(self, bond_id: int, kind: str) -> AIAnalysis | None:
        row = (
            self.session.query(AIAnalysis)
            .filter(AIAnalysis.bond_id == bond_id, AIAnalysis.kind == kind)
            .order_by(AIAnalysis.created_at.desc())
            .first()
        )
        if row is None or row.expires_at is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return row if expires > datetime.now(timezone.utc) else None

    async def explain(
        self,
        bond_id: int,
        explanation: dict,
        *,
        ui_mode: str = "simple",
        use_cache: bool = True,
    ) -> dict:
        fallback = deterministic_text(explanation)
        kind = f"score_explanation:{ui_mode}"

        if use_cache:
            cached = self._cached(bond_id, kind)
            if cached is not None:
                return {
                    "text": cached.content,
                    "generated_by": "llm",
                    "cached": True,
                    "model": cached.model,
                    "deterministic_text": fallback,
                    "explanation": explanation,
                }

        response = await self.client.chat(
            [
                ChatMessage(role="system", content=SYSTEM_EXPLAINER),
                ChatMessage(
                    role="user", content=explain_score_prompt(explanation, ui_mode=ui_mode)
                ),
            ],
            temperature=0.2,
        )
        if not response.ok:
            logger.info("LLM unavailable, serving deterministic explanation: %s", response.error)
            return {
                "text": fallback,
                "generated_by": "engine",
                "cached": False,
                "model": None,
                "reason": response.error,
                "deterministic_text": fallback,
                "explanation": explanation,
            }

        self.session.add(
            AIAnalysis(
                bond_id=bond_id,
                kind=kind,
                language="ru",
                provider=response.provider,
                model=response.model,
                prompt_version=PROMPT_VERSION,
                inputs=explanation,
                content=response.content,
                tokens_prompt=response.tokens_prompt,
                tokens_completion=response.tokens_completion,
                latency_ms=response.latency_ms,
                expires_at=datetime.now(timezone.utc) + CACHE_TTL,
            )
        )
        self.session.commit()
        return {
            "text": response.content,
            "generated_by": "llm",
            "cached": False,
            "model": response.model,
            "deterministic_text": fallback,
            "explanation": explanation,
        }
