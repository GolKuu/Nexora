"""Benchmark metrics (§35).

Every metric here is computed from the golden file's declared expectations, so
a number can be traced back to a specific line of ``golden.jsonl``. Nothing is
judged by another language model: an LLM-as-judge would make the headline
figures depend on a system we do not control, which is the opposite of what
this project is for.

The measured quantities:

* ``tool_selection_accuracy`` - right tool chosen (including "no tool");
* ``argument_f1`` - the arguments the question actually contained, extracted;
* ``json_validity`` - tool decisions that parse as JSON on the first attempt;
* ``hallucination_rate`` - answers that state a figure or fact when the
  honest answer was "нет данных", or that state a forbidden certainty;
* ``refusal_accuracy`` - refuses when it should, answers when it should;
* ``source_attribution`` - answers that label where a number came from;
* ``answer_correctness`` - required content present, forbidden content absent;
* ``forbidden_phrase_rate`` - §66 violations; the release gate requires zero;
* ``russian_quality`` - a coarse structural check of the answer's Russian.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ai.prompts.system import FORBIDDEN_PHRASES

#: Markers an answer uses when it declines for lack of data.
REFUSAL_MARKERS = (
    "нет данных", "не найден", "не найдено", "нет в данных", "не располагаю",
    "недостаточно данных", "не могу", "не буду", "нельзя", "не существует",
    "отсутству", "не раскры", "не назову", "не приму", "не выполня",
)

#: Markers that show the answer labelled a number's origin (§18).
SOURCE_MARKERS = (
    "данные kase", "расчет системы", "расчёт системы", "сценарий",
    "ai-интерпретация", "stat.gov.kz", "отчётности эмитента", "отчетности эмитента",
    "источник", "kase.kz",
)

_NUMBER = re.compile(r"\d[\d  .,]*")
_LATIN_WORD = re.compile(r"\b[a-z]{4,}\b")


@dataclass(slots=True)
class ItemResult:
    item_id: str
    category: str
    tool_correct: bool | None = None
    argument_precision: float | None = None
    argument_recall: float | None = None
    json_valid: bool | None = None
    refusal_correct: bool | None = None
    hallucinated: bool | None = None
    source_attributed: bool | None = None
    content_ok: bool | None = None
    forbidden_hit: list[str] = field(default_factory=list)
    russian_ok: bool | None = None
    latency_ms: float = 0.0
    predicted_tool: str | None = None
    predicted_args: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__slots__}


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or (precision + recall) == 0:
        return 0.0 if (precision is not None or recall is not None) else None
    return 2 * precision * recall / (precision + recall)


def _values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == 0:
            return abs(actual) < 1e-9
        return abs(actual - expected) / abs(expected) < 0.02
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        return [str(v).casefold() for v in expected] == [str(v).casefold() for v in actual]
    return str(expected).casefold() == str(actual).casefold()


def score_tool_decision(item: dict[str, Any], decision: dict[str, Any]) -> ItemResult:
    result = ItemResult(item_id=item["id"], category=item["category"])
    expected_tool = item.get("expects_tool")
    predicted = decision.get("tool")
    result.predicted_tool = predicted
    result.predicted_args = decision.get("arguments") or {}
    result.json_valid = bool(decision.get("valid_json"))
    result.tool_correct = (predicted or None) == (expected_tool or None)

    required = item.get("required_args") or []
    expected_args = item.get("expects_args") or {}
    if expected_tool is None:
        result.argument_precision = None
        result.argument_recall = None
        return result

    predicted_args = result.predicted_args
    hits = sum(
        1 for key in required
        if key in predicted_args and _values_match(expected_args.get(key), predicted_args[key])
    )
    result.argument_recall = hits / len(required) if required else 1.0
    # Precision over the keys the model produced that the question warranted.
    if predicted_args:
        legit = sum(
            1 for key, value in predicted_args.items()
            if key not in expected_args or _values_match(expected_args[key], value)
        )
        result.argument_precision = legit / len(predicted_args)
    else:
        result.argument_precision = 1.0 if not required else 0.0
    return result


def score_answer(item: dict[str, Any], answer: str, *, trace: dict[str, Any] | None = None) -> ItemResult:
    result = ItemResult(item_id=item["id"], category=item["category"])
    lowered = answer.lower()

    result.forbidden_hit = [p for p in FORBIDDEN_PHRASES if p in lowered]
    for phrase in item.get("forbidden") or []:
        if phrase.lower() in lowered:
            result.forbidden_hit.append(phrase)

    refused = any(marker in lowered for marker in REFUSAL_MARKERS)
    must_refuse = bool(item.get("must_refuse"))
    result.refusal_correct = refused if must_refuse else True
    if not must_refuse and refused and (item.get("must_contain_any") or item.get("expects_tool")):
        # Refusing a question we can answer is also an error, just a quieter one.
        result.notes.append("отказ на вопрос, на который есть данные")
        result.refusal_correct = False

    # Hallucination: a figure asserted where the correct answer was a refusal,
    # or a forbidden certainty anywhere.
    numbers = [n for n in _NUMBER.findall(answer) if len(n.strip()) > 1]
    result.hallucinated = bool(result.forbidden_hit) or (
        must_refuse and not refused and len(numbers) > 2
    )

    required = item.get("must_contain_any") or []
    result.content_ok = (
        any(marker.lower() in lowered for marker in required) if required else None
    )

    if item.get("requires_source"):
        result.source_attributed = any(marker in lowered for marker in SOURCE_MARKERS)
    elif numbers:
        result.source_attributed = any(marker in lowered for marker in SOURCE_MARKERS)

    result.russian_ok = _russian_ok(answer)
    return result


def _russian_ok(answer: str) -> bool:
    """Coarse structural check, not a fluency score.

    Catches the failures that actually happen with a small multilingual model:
    the answer drifts into English, collapses to one sentence, or emits the
    section skeleton with nothing under it.
    """
    if len(answer.strip()) < 40:
        return False
    cyrillic = sum(1 for ch in answer if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    letters = sum(1 for ch in answer if ch.isalpha())
    if letters == 0 or cyrillic / letters < 0.75:
        return False
    stray_latin = _LATIN_WORD.findall(answer.lower())
    allowed = {"kase", "isin", "ytm", "ebitda", "kzt", "usd", "eur", "http", "https", "stat"}
    if len([w for w in stray_latin if w not in allowed]) > 6:
        return False
    for heading in re.findall(r"^##\s*(.+)$", answer, re.M):
        body = answer.split(f"## {heading}", 1)[1]
        first = body.split("##", 1)[0].strip()
        if not first:
            return False
    return True


def aggregate(results: list[ItemResult]) -> dict[str, Any]:
    def mean(values: list[float]) -> float | None:
        cleaned = [v for v in values if v is not None]
        return round(sum(cleaned) / len(cleaned), 4) if cleaned else None

    tool_items = [r for r in results if r.tool_correct is not None]
    answer_items = [r for r in results if r.hallucinated is not None]

    precision = mean([r.argument_precision for r in results])
    recall = mean([r.argument_recall for r in results])

    by_category: dict[str, dict[str, Any]] = {}
    for result in results:
        bucket = by_category.setdefault(result.category, {"n": 0, "tool": [], "hall": [], "ru": []})
        bucket["n"] += 1
        if result.tool_correct is not None:
            bucket["tool"].append(1.0 if result.tool_correct else 0.0)
        if result.hallucinated is not None:
            bucket["hall"].append(1.0 if result.hallucinated else 0.0)
        if result.russian_ok is not None:
            bucket["ru"].append(1.0 if result.russian_ok else 0.0)
    categories = {
        name: {
            "items": bucket["n"],
            "tool_selection_accuracy": mean(bucket["tool"]),
            "hallucination_rate": mean(bucket["hall"]),
            "russian_quality": mean(bucket["ru"]),
        }
        for name, bucket in sorted(by_category.items())
    }

    return {
        "items": len(results),
        "tool_selection_accuracy": mean([1.0 if r.tool_correct else 0.0 for r in tool_items]),
        "argument_precision": precision,
        "argument_recall": recall,
        "argument_f1": round(_f1(precision, recall) or 0.0, 4),
        "json_validity": mean([1.0 if r.json_valid else 0.0 for r in results if r.json_valid is not None]),
        "refusal_accuracy": mean([1.0 if r.refusal_correct else 0.0 for r in results if r.refusal_correct is not None]),
        "hallucination_rate": mean([1.0 if r.hallucinated else 0.0 for r in answer_items]),
        "source_attribution": mean([1.0 if r.source_attributed else 0.0 for r in results if r.source_attributed is not None]),
        "answer_correctness": mean([1.0 if r.content_ok else 0.0 for r in results if r.content_ok is not None]),
        "forbidden_phrase_rate": mean([1.0 if r.forbidden_hit else 0.0 for r in results]),
        "russian_quality": mean([1.0 if r.russian_ok else 0.0 for r in results if r.russian_ok is not None]),
        "latency_ms_mean": mean([r.latency_ms for r in results]),
        "by_category": categories,
    }


def load_golden(path) -> list[dict[str, Any]]:
    from pathlib import Path

    rows: list[dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
    return rows


__all__ = [
    "ItemResult",
    "REFUSAL_MARKERS",
    "SOURCE_MARKERS",
    "aggregate",
    "load_golden",
    "score_answer",
    "score_tool_decision",
]
