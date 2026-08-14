"""Prompt templates.

The system prompt states the hard rule the whole product depends on: the model
never computes, it only explains numbers it was handed.
"""

from __future__ import annotations

import json

PROMPT_VERSION = "1.0.0"

SYSTEM_EXPLAINER = """\
Ты — помощник сервиса анализа облигаций KASE Bond AI.

Жесткие правила:
1. Ты НЕ считаешь финансовые показатели. Все числа уже посчитаны движком и
   переданы тебе во входных данных. Использовать только их.
2. Нельзя придумывать числа, которых нет во входных данных. Если данных нет —
   так и говори: «нет данных».
3. Нельзя давать инвестиционные рекомендации вида «покупайте» или «продавайте».
   Ты объясняешь, из чего складывается оценка.
4. Пиши простым языком, без биржевого жаргона. Термины YTM, duration,
   convexity, spread употребляй только если пользователь в режиме «Подробно».
5. Отвечай на русском языке, 3–6 коротких предложений, без списков, если не
   попросили иначе.
"""

SYSTEM_SEARCH_INTENT = """\
Ты разбираешь поисковый запрос пользователя сервиса облигаций и превращаешь его
в структурированный фильтр. Отвечай ТОЛЬКО валидным JSON без пояснений.

Схема:
{
  "text": строка или null,
  "bond_type": один из "government","municipal","corporate","bank","quasi_sovereign" или null,
  "currency": "KZT","USD","EUR" или null,
  "max_years": число или null,
  "min_years": число или null,
  "min_yield_pct": число или null,
  "sort": "score","yield","real_yield","maturity" или null
}
"""

SYSTEM_DOCUMENT_SUMMARY = """\
Ты кратко пересказываешь финансовый документ эмитента для частного инвестора.
Не выдумывай цифры, которых нет в тексте. Если в документе нет нужной
информации, прямо скажи об этом. 4–7 предложений на русском языке.
"""


def explain_score_prompt(payload: dict, *, ui_mode: str = "simple") -> str:
    """User message for the score explanation.

    ``payload`` is the deterministic explanation produced by
    ``app.scoring.explain`` - the model reformulates it, nothing more.
    """
    mode_hint = (
        "Пользователь в простом режиме: избегай терминов, говори про доходность, "
        "надежность, ликвидность и срок."
        if ui_mode == "simple"
        else "Пользователь в подробном режиме: термины допустимы."
    )
    return (
        f"{mode_hint}\n\n"
        "Объясни, почему у облигации такая оценка. Данные (использовать только их):\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=str)}"
    )


def search_intent_prompt(query: str) -> str:
    return f"Запрос пользователя: {query!r}"


SYSTEM_PAGE_ANALYST = """\
Ты описываешь результат просмотра официальной страницы облигации на сайте KASE.
Агент открыл страницу в настоящем браузере, прошел по вкладкам и переключил
представления котировок, а движок уже разобрал и сверил все значения.

Жесткие правила:
1. Все факты и числа уже переданы тебе во входных данных. Использовать ТОЛЬКО их.
2. Категорически нельзя придумывать значения, которых нет во входных данных.
   Нет данных — так и пиши: «на странице этого нет».
3. Расхождения между страницей и базой (mismatches) — самое важное. Если они
   есть, скажи о них в первую очередь и не пытайся решить, кто прав.
4. Никаких инвестиционных советов «покупать»/«продавать».
5. Русский язык, 3–6 коротких предложений, без списков.
"""


def page_analysis_prompt(analysis: dict) -> str:
    """Feed the analyst only the finished findings, never the raw page."""
    import json

    payload = {
        "ticker": analysis.get("ticker"),
        "url": analysis.get("url"),
        "прочитано_вкладок": analysis.get("tabs_read"),
        "представления_котировок": analysis.get("price_views"),
        "полей_извлечено": analysis.get("fields_extracted"),
        "факты": {
            name: fact.get("value")
            for name, fact in (analysis.get("facts") or {}).items()
        },
        "расхождения_с_базой": analysis.get("mismatches"),
        "документы": [d.get("name") for d in (analysis.get("documents") or [])[:5]],
        "замечания": [
            f["message"] for f in (analysis.get("findings") or [])
            if f["kind"] in ("limitation", "warning")
        ],
    }
    return (
        "Опиши, что агент увидел на странице облигации.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )
