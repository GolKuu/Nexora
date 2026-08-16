from __future__ import annotations

from app.ai.base import ChatMessage
from app.ai.factory import get_llm_client
from app.ai.prompts import SYSTEM_STOCK_ANALYST, stock_analysis_prompt

FUTURE_PRICE_ANSWER = "Точную будущую цену определить невозможно. Я могу показать текущую оценку компании и сценарии изменения цены."


def deterministic_stock_explanation(card: dict) -> str:
    scores = card["scores"]
    metrics = card["metrics"]
    parts = [f"Общая модельная оценка {scores['investment']['value']}/100." if scores["investment"]["value"] is not None else "Для общей оценки пока недостаточно данных."]
    for key, label in (("quality", "Качество"), ("valuation", "Оценка"), ("growth", "Рост"), ("dividend", "Дивиденды"), ("liquidity", "Ликвидность"), ("risk", "Риск")):
        value = scores[key]["value"]
        parts.append(f"{label}: {value:.0f}/100." if value is not None else f"{label}: нет подтверждённых данных.")
    if metrics.get("pe") is not None:
        parts.append(f"P/E: {metrics['pe']:.2f}.")
    return " ".join(parts) + " Оценка не является обещанием роста цены."


class StockAnalystService:
    async def explain(self, card: dict, question: str | None = None) -> dict:
        if question and any(phrase in question.casefold() for phrase in ("точно выраст", "гарантированно выраст", "точная цена")):
            return {"answer": FUTURE_PRICE_ANSWER, "generated_by": "policy", "model": None}
        fallback = deterministic_stock_explanation(card)
        verified = {"ticker": card["ticker"], "company_name": card["company_name"], "metrics": card["metrics"],
                    "scores": card["scores"], "dividends": card["dividends"], "corporate_actions": card["corporate_actions"],
                    "data_timestamp": card["data_timestamp"], "source": card["source"], "question": question}
        response = await get_llm_client().chat([
            ChatMessage(role="system", content=SYSTEM_STOCK_ANALYST),
            ChatMessage(role="user", content=stock_analysis_prompt(verified)),
        ], temperature=0.1, max_tokens=700)
        if not response.ok:
            return {"answer": fallback, "generated_by": "engine", "model": None, "reason": response.error}
        return {"answer": response.content, "generated_by": "llm", "model": response.model}


__all__ = ["FUTURE_PRICE_ANSWER", "StockAnalystService", "deterministic_stock_explanation"]
